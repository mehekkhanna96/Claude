#!/usr/bin/env python3
"""
Flight Booking Timing Advisor

Helps sabbatical planners figure out WHEN to book flights for the cheapest
fares — not just which flights are cheap, but how far in advance to book,
which days of the week to fly, and whether current prices are historically
good.

Focuses on routes from Singapore (SIN) and India (BOM, DEL, MAA, BLR)
to Southeast Asian destinations.

Usage:
    python flight_advisor.py
    python flight_advisor.py --origin SIN --destination BKK --month 2025-03
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt

from amadeus_client import AmadeusClient, AmadeusAPIError, AmadeusRateLimitError

load_dotenv()

console = Console()

# ---------------------------------------------------------------------------
# Airport / route knowledge
# ---------------------------------------------------------------------------

SEA_AIRPORTS = {
    "Thailand": ["BKK", "DMK", "HKT", "CNX"],
    "Malaysia": ["KUL", "PEN", "LGK"],
    "Indonesia": ["DPS", "CGK", "SUB"],
    "Vietnam": ["SGN", "HAN", "DAD"],
    "Philippines": ["MNL", "CEB"],
    "Cambodia": ["PNH", "REP"],
    "Myanmar": ["RGN"],
    "Singapore": ["SIN"],
    "Sri Lanka": ["CMB"],
    "Laos": ["VTE"],
}

INDIA_AIRPORTS = {
    "Mumbai": "BOM",
    "Delhi": "DEL",
    "Chennai": "MAA",
    "Bangalore": "BLR",
    "Kolkata": "CCU",
    "Hyderabad": "HYD",
    "Kochi": "COK",
}

COUNTRY_TO_MAIN_AIRPORT = {
    "thailand": "BKK",
    "malaysia": "KUL",
    "indonesia": "DPS",
    "bali": "DPS",
    "vietnam": "SGN",
    "philippines": "MNL",
    "cambodia": "PNH",
    "myanmar": "RGN",
    "singapore": "SIN",
    "sri lanka": "CMB",
    "laos": "VTE",
    "bangkok": "BKK",
    "kuala lumpur": "KUL",
    "ho chi minh": "SGN",
    "hanoi": "HAN",
    "manila": "MNL",
}

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a flight booking timing advisor specializing in routes from
Singapore (SIN) and India (BOM, DEL, MAA, BLR) to Southeast Asian destinations.

Your mission is to help a sabbatical planner answer: **when is the best time to BOOK
flights**, not just which flights exist. You focus on:

1. **Booking window** — How many days/weeks before departure to buy for lowest price
2. **Day of week to book** — Are Tuesday/Wednesday purchases cheaper?
3. **Day of week to fly** — Which departure days are historically cheaper?
4. **Current price positioning** — Is the current price at the 25th percentile (buy now!)
   or 75th percentile (wait or look for alternatives)?
5. **Seasonal patterns** — High/low seasons for each route

When analyzing routes:
- Use the price insights tool to get historical price percentiles (this tells you if
  prices are currently cheap or expensive relative to history)
- Use the booking pattern analysis to understand lead-time sweet spots
- Use cheapest days analysis to identify day-of-week patterns
- Combine all data into a clear, actionable strategy

Output format: Give concrete, numbered recommendations with data backing each one.
Use percentile context: "Price is at the 23rd percentile — historically cheap, book now"
vs "Price at 78th percentile — consider waiting or alternative dates."

IATA airport codes for reference:
- Singapore: SIN
- India: BOM (Mumbai), DEL (Delhi), MAA (Chennai), BLR (Bangalore), CCU (Kolkata)
- Bangkok: BKK/DMK, KL: KUL, Bali: DPS, Ho Chi Minh: SGN, Hanoi: HAN
- Manila: MNL, Phnom Penh: PNH, Yangon: RGN, Colombo: CMB

Always provide:
1. A verdict on current pricing (buy now vs wait)
2. Optimal booking window in days
3. Best/worst days of week to depart
4. Confidence level (low/medium/high) based on data available
5. Any caveats (test API limitations, sparse data, etc.)"""

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_flight_prices",
        "description": (
            "Search current flight prices for a specific route and date. "
            "Returns the top cheapest offers with price, airline, stops, and timing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {
                    "type": "string",
                    "description": "IATA origin airport code (e.g. 'SIN', 'BOM', 'DEL')",
                },
                "destination": {
                    "type": "string",
                    "description": "IATA destination airport code (e.g. 'BKK', 'KUL', 'DPS')",
                },
                "departure_date": {
                    "type": "string",
                    "description": "Departure date in YYYY-MM-DD format",
                },
                "currency": {
                    "type": "string",
                    "description": "Currency code for prices (default: SGD)",
                    "default": "SGD",
                },
            },
            "required": ["origin", "destination", "departure_date"],
        },
    },
    {
        "name": "get_price_insights",
        "description": (
            "Get historical price percentiles (min, 25th, median, 75th, max) for a "
            "route and date. This is the KEY tool for determining if current prices "
            "are cheap or expensive relative to history. Returns quartile rankings "
            "so you can advise 'book now' vs 'wait'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "IATA origin code"},
                "destination": {"type": "string", "description": "IATA destination code"},
                "departure_date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format",
                },
                "currency": {
                    "type": "string",
                    "description": "Currency code (default: SGD)",
                    "default": "SGD",
                },
            },
            "required": ["origin", "destination", "departure_date"],
        },
    },
    {
        "name": "analyze_booking_patterns",
        "description": (
            "Analyze how prices change based on how far in advance you book. "
            "Samples price insights at multiple booking windows (14, 21, 30, 45, 60, "
            "90 days before departure) to find the sweet spot. Returns a summary of "
            "price trends vs lead time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "IATA origin code"},
                "destination": {"type": "string", "description": "IATA destination code"},
                "target_month": {
                    "type": "string",
                    "description": "Target travel month in YYYY-MM format (e.g. '2025-03')",
                },
                "currency": {
                    "type": "string",
                    "description": "Currency code (default: SGD)",
                    "default": "SGD",
                },
            },
            "required": ["origin", "destination", "target_month"],
        },
    },
    {
        "name": "get_cheapest_days_to_fly",
        "description": (
            "Scan all departure dates in a month to find which days of the week and "
            "dates tend to be cheapest. Helps identify Tuesday/Wednesday vs "
            "Friday/Saturday pricing patterns. Returns prices sorted cheapest first "
            "with day-of-week breakdown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "IATA origin code"},
                "destination": {"type": "string", "description": "IATA destination code"},
                "month_start": {
                    "type": "string",
                    "description": "First day of the month to scan in YYYY-MM-DD format",
                },
                "scan_days": {
                    "type": "integer",
                    "description": "Number of days to scan (default 21, max 30)",
                    "default": 21,
                },
                "currency": {
                    "type": "string",
                    "description": "Currency code (default: SGD)",
                    "default": "SGD",
                },
            },
            "required": ["origin", "destination", "month_start"],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def execute_tool(name: str, inputs: dict, amadeus: AmadeusClient) -> str:
    """Execute a tool call and return JSON string result."""
    try:
        if name == "search_flight_prices":
            result = amadeus.search_flight_prices(
                origin=inputs["origin"],
                destination=inputs["destination"],
                departure_date=inputs["departure_date"],
                currency=inputs.get("currency", "SGD"),
            )
            if not result:
                return json.dumps({"error": "No flights found for this route/date."})
            return json.dumps(result, indent=2)

        elif name == "get_price_insights":
            result = amadeus.get_price_insights(
                origin=inputs["origin"],
                destination=inputs["destination"],
                departure_date=inputs["departure_date"],
                currency=inputs.get("currency", "SGD"),
            )
            if not result:
                return json.dumps({
                    "note": "No historical price data available for this route/date. "
                    "The Amadeus test environment has limited coverage. Try a date "
                    "2-4 months in the future for major routes."
                })
            return json.dumps(result, indent=2)

        elif name == "analyze_booking_patterns":
            origin = inputs["origin"]
            destination = inputs["destination"]
            target_month = inputs["target_month"]
            currency = inputs.get("currency", "SGD")

            # Pick 15th of target month as the reference departure date
            ref_date = datetime.strptime(target_month + "-15", "%Y-%m-%d")
            today = datetime.now()

            booking_windows = [14, 21, 30, 45, 60, 90]
            pattern_data = []

            for days_ahead in booking_windows:
                # The departure date would be this many days from today
                dep_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
                # But we also want the actual ref date if it's in the future
                actual_dep = ref_date.strftime("%Y-%m-%d")

                # Get price insights for the reference departure date
                try:
                    insights = amadeus.get_price_insights(
                        origin=origin,
                        destination=destination,
                        departure_date=actual_dep,
                        currency=currency,
                    )
                    if insights:
                        pattern_data.append({
                            "lead_time_days": days_ahead,
                            "departure_date": actual_dep,
                            "price_data": insights,
                        })
                    time.sleep(0.3)  # Rate limit courtesy
                except (AmadeusRateLimitError, AmadeusAPIError) as exc:
                    pattern_data.append({
                        "lead_time_days": days_ahead,
                        "error": str(exc),
                    })

            # Also sample current prices at different booking windows
            current_prices = []
            for days_ahead in [21, 45, 90]:
                dep_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
                try:
                    offers = amadeus.search_flight_prices(
                        origin=origin,
                        destination=destination,
                        departure_date=dep_date,
                        currency=currency,
                        max_results=1,
                    )
                    if offers:
                        current_prices.append({
                            "days_until_departure": days_ahead,
                            "departure_date": dep_date,
                            "cheapest_price": offers[0]["price"],
                            "currency": currency,
                            "airline": offers[0]["airline"],
                        })
                    time.sleep(0.3)
                except (AmadeusRateLimitError, AmadeusAPIError):
                    pass

            return json.dumps(
                {
                    "route": f"{origin} → {destination}",
                    "target_month": target_month,
                    "price_insights_by_window": pattern_data,
                    "current_prices_by_lead_time": current_prices,
                    "note": (
                        "price_insights_by_window shows historical percentile data for "
                        "the reference date. current_prices_by_lead_time shows actual "
                        "prices for departures at different lead times from today."
                    ),
                },
                indent=2,
            )

        elif name == "get_cheapest_days_to_fly":
            origin = inputs["origin"]
            destination = inputs["destination"]
            month_start = inputs["month_start"]
            scan_days = min(inputs.get("scan_days", 21), 30)
            currency = inputs.get("currency", "SGD")

            results = amadeus.get_cheapest_date_suggestions(
                origin=origin,
                destination=destination,
                departure_date_range_start=month_start,
                duration_days=scan_days,
                currency=currency,
            )

            if not results:
                return json.dumps({
                    "error": "No price data returned for this date range. "
                    "Try dates at least 3 weeks in the future."
                })

            # Summarise by day of week
            day_prices: dict[str, list[float]] = {}
            for r in results:
                dow = r["day_of_week"]
                day_prices.setdefault(dow, []).append(r["price"])

            day_averages = {
                dow: round(sum(prices) / len(prices), 2)
                for dow, prices in day_prices.items()
            }

            return json.dumps(
                {
                    "route": f"{origin} → {destination}",
                    "scan_start": month_start,
                    "scan_days": scan_days,
                    "currency": currency,
                    "cheapest_10": results[:10],
                    "most_expensive_5": results[-5:],
                    "average_price_by_day_of_week": day_averages,
                    "cheapest_day": min(day_averages, key=day_averages.get) if day_averages else None,
                    "most_expensive_day": max(day_averages, key=day_averages.get) if day_averages else None,
                },
                indent=2,
            )

        else:
            return json.dumps({"error": f"Unknown tool: {name}"})

    except AmadeusRateLimitError as exc:
        return json.dumps({
            "error": f"Rate limited by Amadeus API: {exc}. "
            "Please wait 30 seconds and try again."
        })
    except AmadeusAPIError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return json.dumps({"error": f"Unexpected error: {exc}"})


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


def run_agent(query: str, amadeus: AmadeusClient) -> None:
    """Run the Claude agent loop, executing tools as requested."""
    client = anthropic.Anthropic()

    messages: list[dict] = [{"role": "user", "content": query}]

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Analyzing flight pricing patterns…", total=None)

        iteration = 0
        while True:
            iteration += 1
            progress.update(task, description=f"Thinking… (step {iteration})")

            response = client.messages.create(
                model="claude-opus-4-7",
                max_tokens=8096,
                thinking={"type": "adaptive"},
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=TOOLS,
                messages=messages,
            )

            # Append assistant response to history
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    progress.update(
                        task,
                        description=f"Calling {block.name}({', '.join(f'{k}={v}' for k, v in list(block.input.items())[:2])})…",
                    )

                    result_str = execute_tool(block.name, block.input, amadeus)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_str,
                        }
                    )

                messages.append({"role": "user", "content": tool_results})
                continue

            # Unexpected stop reason
            console.print(f"[yellow]Unexpected stop reason: {response.stop_reason}[/yellow]")
            break

    # Extract and display the final text response
    for block in response.content:
        if hasattr(block, "text"):
            console.print()
            console.print(Panel(Markdown(block.text), title="[bold green]Booking Strategy[/bold green]", border_style="green"))
            break


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_query(origin: str, destination: str, month: str) -> str:
    return (
        f"I'm planning a sabbatical trip from {origin} to {destination} "
        f"in {month}. Please give me a complete flight booking timing strategy:\n\n"
        f"1. Are prices currently cheap or expensive for this route? (use price insights)\n"
        f"2. How far in advance should I book for the best price? (analyze booking patterns)\n"
        f"3. Which days of the week are cheapest to fly? (check cheapest days)\n"
        f"4. Give me an overall booking recommendation with confidence level.\n\n"
        f"Please use the available tools to gather real price data before advising."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Flight Booking Timing Advisor for sabbatical planning"
    )
    parser.add_argument("--origin", help="IATA origin airport code (e.g. SIN, BOM)")
    parser.add_argument("--destination", help="IATA destination airport code (e.g. BKK, DPS)")
    parser.add_argument("--month", help="Target travel month in YYYY-MM format")
    args = parser.parse_args()

    # Validate API keys
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    amadeus_key = os.getenv("AMADEUS_API_KEY")
    amadeus_secret = os.getenv("AMADEUS_API_SECRET")

    missing = []
    if not anthropic_key:
        missing.append("ANTHROPIC_API_KEY")
    if not amadeus_key:
        missing.append("AMADEUS_API_KEY")
    if not amadeus_secret:
        missing.append("AMADEUS_API_SECRET")

    if missing:
        console.print(
            Panel(
                f"[red]Missing environment variables:[/red]\n"
                + "\n".join(f"  • {k}" for k in missing)
                + "\n\nCopy [bold].env.example[/bold] to [bold].env[/bold] and fill in your keys.\n\n"
                "Get Amadeus keys (free) at: [link]https://developers.amadeus.com[/link]",
                title="[red]Setup Required[/red]",
                border_style="red",
            )
        )
        sys.exit(1)

    amadeus = AmadeusClient(amadeus_key, amadeus_secret)

    console.print(
        Panel(
            "[bold]Flight Booking Timing Advisor[/bold]\n"
            "Sabbatical edition — SIN / India → Southeast Asia\n\n"
            "This tool tells you [bold green]WHEN[/bold green] to book for cheapest fares,\n"
            "not just what's available today.",
            title="✈  Flight Advisor",
            border_style="blue",
        )
    )

    # Collect inputs interactively if not passed as args
    origin = args.origin
    destination = args.destination
    month = args.month

    if not origin:
        console.print("\n[bold]Common origins:[/bold] SIN, BOM, DEL, MAA, BLR, CCU")
        origin = Prompt.ask("Origin airport code").strip().upper()

    if not destination:
        console.print("\n[bold]Common SEA destinations:[/bold] BKK, KUL, DPS, SGN, HAN, MNL, PNH")
        destination = Prompt.ask("Destination airport code").strip().upper()

        # Allow country names
        dest_lower = destination.lower()
        if dest_lower in COUNTRY_TO_MAIN_AIRPORT:
            destination = COUNTRY_TO_MAIN_AIRPORT[dest_lower]
            console.print(f"[dim]Using {destination} as main airport[/dim]")

    if not month:
        default_month = (datetime.now() + timedelta(days=60)).strftime("%Y-%m")
        month = Prompt.ask("Target travel month (YYYY-MM)", default=default_month).strip()

    # Validate month format
    try:
        datetime.strptime(month + "-01", "%Y-%m-%d")
    except ValueError:
        console.print(f"[red]Invalid month format: {month}. Use YYYY-MM (e.g. 2025-06)[/red]")
        sys.exit(1)

    console.print(
        f"\n[bold]Analyzing:[/bold] {origin} → {destination} for {month}\n"
        "[dim]Gathering price data from Amadeus API…[/dim]\n"
    )

    query = build_query(origin, destination, month)
    run_agent(query, amadeus)


if __name__ == "__main__":
    main()
