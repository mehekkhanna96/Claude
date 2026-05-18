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
    python flight_advisor.py --query "When should I book SIN to Bali for August?"
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt
from rich.rule import Rule
from rich.text import Text

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
    "yangon": "RGN",
    "rangoon": "RGN",
    "colombo": "CMB",
    "phnom penh": "PNH",
    "siem reap": "REP",
    "vientiane": "VTE",
    # India
    "mumbai": "BOM",
    "bombay": "BOM",
    "delhi": "DEL",
    "new delhi": "DEL",
    "chennai": "MAA",
    "madras": "MAA",
    "bangalore": "BLR",
    "bengaluru": "BLR",
    "kolkata": "CCU",
    "calcutta": "CCU",
    "hyderabad": "HYD",
    "kochi": "COK",
    "cochin": "COK",
}

# Human-readable city names for display
IATA_TO_CITY = {
    "BKK": "Bangkok", "KUL": "Kuala Lumpur", "SGN": "Ho Chi Minh City",
    "HAN": "Hanoi", "DPS": "Bali/Denpasar", "CGK": "Jakarta", "MNL": "Manila",
    "RGN": "Yangon", "CMB": "Colombo", "PNH": "Phnom Penh", "REP": "Siem Reap",
    "VTE": "Vientiane", "SIN": "Singapore", "BOM": "Mumbai", "DEL": "Delhi",
    "MAA": "Chennai", "BLR": "Bangalore", "HYD": "Hyderabad",
    "CCU": "Kolkata", "COK": "Kochi",
}

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a **Flight Booking Timing Advisor** specialising in sabbatical travel planning.

Your users fly primarily from **Singapore (SIN)** and **Indian cities (BOM, DEL, MAA, BLR, HYD, CCU, COK)** to **Southeast Asian destinations**: Bangkok (BKK), Kuala Lumpur (KUL), Ho Chi Minh City (SGN), Hanoi (HAN), Bali (DPS), Manila (MNL), Yangon (RGN), Colombo (CMB), Phnom Penh (PNH), Siem Reap (REP), Vientiane (VTE).

## Your Core Mission

Answer: **WHEN should I book this flight to get the cheapest fare?**

Deliver a clear, data-backed booking strategy covering:
1. **Booking window** — How many days/weeks in advance to buy (sweet spot for this route)
2. **Day of week to buy the ticket** — Tuesday/Wednesday often cheapest booking days
3. **Day of week to fly** — Which departure days tend to be cheapest
4. **Current price signal** — Is today's price at the 25th percentile (cheap, buy now) or 75th+ (expensive, wait)?
5. **Seasonal context** — Peak vs shoulder vs off-peak for this route/month

## Tool Usage Strategy

Always follow this sequence:
1. Call `get_price_insights` for 2–3 dates within the target month → baseline price distribution
2. Call `search_flight_prices` for a concrete current-price snapshot
3. Call `analyze_booking_patterns` → how prices vary by advance booking lead time
4. Call `get_cheapest_days_to_fly` → identify cheapest days of week and parts of the month
5. Synthesise everything into a **Booking Strategy** with confidence level

## Domain Knowledge

**Advance booking sweet spots (SIN/India → SEA):**
- Low-cost carriers (AirAsia, Scoot, Jetstar): book 4–8 weeks ahead; flash sales drop Tue/Wed
- Full-service carriers (SIA, MAS, Thai): book 6–12 weeks ahead; prices stable then rise
- Within 14 days of departure: prices spike 20–40% above median on most routes

**Day-of-week patterns:**
- Cheapest to FLY: Monday, Tuesday, Wednesday (midweek departures)
- Most expensive to FLY: Friday, Saturday (weekend rush)
- Cheapest to BOOK: Tuesday or Wednesday (airlines release sales Mon night)
- Most expensive to BOOK: Sunday/Monday (weekend browsing drives prices up)

**Seasonal calendar for SIN/India → SEA:**
- Peak (expensive): Dec 15 – Jan 5 (school holidays), Jun 1 – Jul 15 (summer), local public holidays
- Shoulder (good value): Feb–Mar, Sep–Oct — often best bang-for-buck
- Off-peak (cheapest): Jan 10–Mar 31 (post-holiday, rainy season in some SEA countries)

**Route-specific notes:**
- SIN → BKK: Very competitive, 5+ LCCs, book 3–6 weeks ahead is usually fine
- SIN → DPS (Bali): Book 6–8 weeks ahead; weekend prices significantly higher
- BOM/DEL → SEA: Fewer direct options, transit via SIN/KUL common; book 8–10 weeks ahead
- India → BKK/KUL: AirAsia India has good fares; compare with IndiGo connecting via SIN

## Output Format

Structure your final recommendation as a **Booking Strategy** with these sections:

### ✈ Booking Strategy: [Route] — [Month]

**Current Price Signal** (with percentile interpretation)
**Optimal Booking Window** (X–Y weeks ahead, with confidence)
**Best Days to Book the Ticket** (day of week)
**Best Days to Fly** (day of week, with price difference estimate)
**Seasonal Context** (peak/shoulder/off-peak assessment)
**Actionable Next Steps** (concrete: "Set alert, buy in 3 weeks if price stays below SGD X")

## Confidence Levels
- **HIGH**: Multiple tool results with consistent data
- **MEDIUM**: Some gaps but directional clarity
- **LOW**: Sparse API data; rely more on domain knowledge (state this clearly)

Always be honest about Amadeus test API limitations (some routes have sparse historical data). When data is missing, say so and fall back on domain knowledge with a LOW confidence label."""

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
            "Scan all departure dates in a given month to identify which days of the week "
            "and which parts of the month (early/mid/late) are cheapest. "
            "Returns a day-of-week price breakdown and the top 10 cheapest dates. "
            "Use this to answer 'which days of the week are cheapest to fly?' "
            "and 'should I fly in early, mid, or late month?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "IATA origin airport code"},
                "destination": {"type": "string", "description": "IATA destination airport code"},
                "target_month": {
                    "type": "string",
                    "description": "Target travel month in YYYY-MM format (e.g. '2025-08')",
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
]

# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def _retry_get_insights(
    amadeus: AmadeusClient,
    origin: str,
    destination: str,
    departure_date: str,
    currency: str,
    retries: int = 2,
    delay: float = 5.0,
) -> dict:
    """Call get_price_insights with simple retry on rate limit."""
    for attempt in range(retries + 1):
        try:
            return amadeus.get_price_insights(
                origin=origin,
                destination=destination,
                departure_date=departure_date,
                currency=currency,
            )
        except AmadeusRateLimitError:
            if attempt < retries:
                time.sleep(delay)
            else:
                raise
    return {}


def execute_tool(name: str, inputs: dict, amadeus: AmadeusClient) -> str:
    """Execute a tool call and return a JSON string result for Claude."""
    try:
        # ------------------------------------------------------------------
        # Tool: search_flight_prices
        # ------------------------------------------------------------------
        if name == "search_flight_prices":
            result = amadeus.search_flight_prices(
                origin=inputs["origin"],
                destination=inputs["destination"],
                departure_date=inputs["departure_date"],
                return_date=inputs.get("return_date"),
                currency=inputs.get("currency", "SGD"),
            )
            if not result:
                return json.dumps({
                    "status": "no_data",
                    "message": (
                        f"No flights found for {inputs['origin']}→{inputs['destination']} "
                        f"on {inputs['departure_date']}. The route may not be served or "
                        "the date may be out of the Amadeus test API's coverage window."
                    ),
                })
            return json.dumps({
                "status": "ok",
                "route": f"{inputs['origin']}→{inputs['destination']}",
                "departure_date": inputs["departure_date"],
                "currency": inputs.get("currency", "SGD"),
                "offers": result,
                "cheapest_price": result[0]["price"],
                "cheapest_airline": result[0]["airline"],
            }, indent=2)

        # ------------------------------------------------------------------
        # Tool: get_price_insights
        # ------------------------------------------------------------------
        elif name == "get_price_insights":
            result = _retry_get_insights(
                amadeus=amadeus,
                origin=inputs["origin"],
                destination=inputs["destination"],
                departure_date=inputs["departure_date"],
                currency=inputs.get("currency", "SGD"),
            )
            if not result:
                return json.dumps({
                    "status": "no_data",
                    "message": (
                        f"No historical price data for {inputs['origin']}→{inputs['destination']} "
                        f"on {inputs['departure_date']}. "
                        "The Amadeus test API has limited analytics coverage. "
                        "Try a date 2–4 months in the future on major routes (SIN→BKK, SIN→KUL)."
                    ),
                })
            # Add human-readable interpretation
            q1 = result.get("first_quartile")
            median = result.get("median")
            q3 = result.get("third_quartile")
            mn = result.get("min")
            mx = result.get("max")
            currency = result.get("currency", "SGD")

            interp = "Price distribution available."
            if q1 and median and q3:
                interp = (
                    f"Historical price range: {currency} {mn:.0f}–{mx:.0f}. "
                    f"Cheap fares (25th pct): {currency} {q1:.0f}. "
                    f"Median: {currency} {median:.0f}. "
                    f"Expensive (75th pct): {currency} {q3:.0f}. "
                    "If current price is near Q1 → buy now. Near Q3 → consider waiting."
                )
            return json.dumps({
                "status": "ok",
                "route": f"{inputs['origin']}→{inputs['destination']}",
                "departure_date": inputs["departure_date"],
                "price_percentiles": {
                    "min": mn,
                    "first_quartile_25pct": q1,
                    "median_50pct": median,
                    "third_quartile_75pct": q3,
                    "max": mx,
                },
                "currency": currency,
                "interpretation": interp,
            }, indent=2)

        # ------------------------------------------------------------------
        # Tool: analyze_booking_patterns
        # ------------------------------------------------------------------
        elif name == "analyze_booking_patterns":
            origin = inputs["origin"]
            destination = inputs["destination"]
            target_month = inputs["target_month"]
            currency = inputs.get("currency", "SGD")

            try:
                month_dt = datetime.strptime(target_month, "%Y-%m")
            except ValueError:
                return json.dumps({"error": f"Invalid target_month: {target_month!r}. Use YYYY-MM."})

            today = datetime.now()
            booking_windows = [14, 21, 30, 45, 60, 90]

            # Sample 2 departure dates: 10th and 20th of target month
            sample_deps = []
            for day in (10, 20):
                candidate = month_dt.replace(day=day)
                if candidate > today:
                    sample_deps.append(candidate.strftime("%Y-%m-%d"))

            if not sample_deps:
                # Month is in the past — use today+30 as proxy
                sample_deps = [(today + timedelta(days=30)).strftime("%Y-%m-%d")]

            # --- Part 1: price insights for sample departure dates ---
            insights_results = []
            for dep_date in sample_deps:
                try:
                    data = _retry_get_insights(amadeus, origin, destination, dep_date, currency)
                    if data:
                        insights_results.append({"departure_date": dep_date, "insights": data})
                    time.sleep(0.35)
                except (AmadeusRateLimitError, AmadeusAPIError) as exc:
                    insights_results.append({"departure_date": dep_date, "error": str(exc)})

            # --- Part 2: live prices at different advance booking lead times ---
            # Each entry: book TODAY for a flight departing N days from now
            lead_time_prices = []
            for days_ahead in booking_windows:
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
                        lead_time_prices.append({
                            "days_until_departure": days_ahead,
                            "departure_date": dep_date,
                            "cheapest_price": offers[0]["price"],
                            "airline": offers[0]["airline"],
                            "currency": currency,
                        })
                    time.sleep(0.35)
                except AmadeusRateLimitError:
                    lead_time_prices.append({
                        "days_until_departure": days_ahead,
                        "error": "Rate limited",
                    })
                    time.sleep(5)
                except AmadeusAPIError:
                    pass

            # Summarise lead-time price trend
            trend_note = ""
            if len(lead_time_prices) >= 2:
                valid = [p for p in lead_time_prices if "cheapest_price" in p]
                if valid:
                    prices_by_window = {p["days_until_departure"]: p["cheapest_price"] for p in valid}
                    sorted_windows = sorted(prices_by_window.items())
                    cheapest_window = min(valid, key=lambda x: x["cheapest_price"])
                    trend_note = (
                        f"Lead-time price trend (today booking): "
                        + ", ".join(f"{d}d→{currency}{p:.0f}" for d, p in sorted_windows)
                        + f". Cheapest window: {cheapest_window['days_until_departure']} days ahead "
                        f"at {currency} {cheapest_window['cheapest_price']:.0f}."
                    )

            return json.dumps({
                "route": f"{origin}→{destination}",
                "target_month": target_month,
                "currency": currency,
                "price_insights_for_target_month": insights_results,
                "lead_time_price_comparison": lead_time_prices,
                "trend_summary": trend_note or "Insufficient data for trend analysis.",
                "methodology": (
                    "lead_time_price_comparison shows CURRENT prices for flights departing "
                    "14/21/30/45/60/90 days from today — illustrating how price changes "
                    "with booking lead time. price_insights_for_target_month shows historical "
                    "percentile data for the target departure month."
                ),
            }, indent=2)

        # ------------------------------------------------------------------
        # Tool: get_cheapest_days_to_fly
        # ------------------------------------------------------------------
        elif name == "get_cheapest_days_to_fly":
            origin = inputs["origin"]
            destination = inputs["destination"]
            target_month = inputs["target_month"]
            currency = inputs.get("currency", "SGD")

            try:
                month_dt = datetime.strptime(target_month, "%Y-%m")
            except ValueError:
                return json.dumps({"error": f"Invalid target_month: {target_month!r}. Use YYYY-MM."})

            # Determine how many days are in this month
            if month_dt.month == 12:
                next_month = month_dt.replace(year=month_dt.year + 1, month=1, day=1)
            else:
                next_month = month_dt.replace(month=month_dt.month + 1, day=1)
            days_in_month = (next_month - month_dt).days

            results = amadeus.get_cheapest_date_suggestions(
                origin=origin,
                destination=destination,
                departure_date_range_start=month_dt.strftime("%Y-%m-%d"),
                duration_days=days_in_month,
                currency=currency,
            )

            if not results:
                return json.dumps({
                    "status": "no_data",
                    "message": (
                        f"No price data for {origin}→{destination} in {target_month}. "
                        "Ensure the departure dates are at least 3 days in the future "
                        "and the route is served."
                    ),
                })

            # Group by day of week
            dow_prices: dict[str, list[float]] = defaultdict(list)
            for r in results:
                dow_prices[r["day_of_week"]].append(r["price"])

            dow_summary = {
                dow: {
                    "avg_price": round(sum(ps) / len(ps), 2),
                    "min_price": round(min(ps), 2),
                    "sample_count": len(ps),
                }
                for dow, ps in dow_prices.items()
            }
            dow_ranked = sorted(dow_summary.items(), key=lambda x: x[1]["avg_price"])

            # Early / mid / late month breakdown
            def avg_prices(lst: list[dict]) -> float | None:
                ps = [r["price"] for r in lst]
                return round(sum(ps) / len(ps), 2) if ps else None

            early = [r for r in results if int(r["date"].split("-")[2]) <= 10]
            mid = [r for r in results if 11 <= int(r["date"].split("-")[2]) <= 20]
            late = [r for r in results if int(r["date"].split("-")[2]) >= 21]

            month_thirds = {
                "early_month_days_1_10_avg": avg_prices(early),
                "mid_month_days_11_20_avg": avg_prices(mid),
                "late_month_days_21_end_avg": avg_prices(late),
            }

            cheapest_dow = dow_ranked[0][0] if dow_ranked else "N/A"
            priciest_dow = dow_ranked[-1][0] if dow_ranked else "N/A"
            cheapest_dow_price = dow_ranked[0][1]["avg_price"] if dow_ranked else None
            priciest_dow_price = dow_ranked[-1][1]["avg_price"] if dow_ranked else None

            savings_pct = None
            if cheapest_dow_price and priciest_dow_price and priciest_dow_price > 0:
                savings_pct = round((priciest_dow_price - cheapest_dow_price) / priciest_dow_price * 100, 1)

            return json.dumps({
                "status": "ok",
                "route": f"{origin}→{destination}",
                "target_month": target_month,
                "currency": currency,
                "days_with_data": len(results),
                "day_of_week_breakdown": {d: s for d, s in dow_ranked},
                "cheapest_day_to_fly": cheapest_dow,
                "most_expensive_day_to_fly": priciest_dow,
                "potential_savings_flying_cheapest_vs_priciest_day": f"{savings_pct}%" if savings_pct else "N/A",
                "month_thirds_avg_price": month_thirds,
                "top_10_cheapest_dates": results[:10],
                "interpretation": (
                    f"Cheapest departure day: {cheapest_dow} "
                    f"(avg {currency} {cheapest_dow_price}). "
                    f"Most expensive: {priciest_dow} "
                    f"(avg {currency} {priciest_dow_price}). "
                    f"Flying {cheapest_dow} vs {priciest_dow} saves ~{savings_pct}%."
                    if savings_pct else
                    f"Cheapest departure day: {cheapest_dow}. Most expensive: {priciest_dow}."
                ),
            }, indent=2)

        else:
            return json.dumps({"error": f"Unknown tool: {name!r}"})

    except AmadeusRateLimitError as exc:
        return json.dumps({
            "status": "rate_limited",
            "error": (
                f"Amadeus API rate limit hit: {exc}. "
                "Wait ~60 seconds and try again, or reduce the number of API calls."
            ),
        })
    except AmadeusAPIError as exc:
        return json.dumps({"status": "api_error", "error": str(exc)})
    except Exception as exc:
        return json.dumps({"status": "unexpected_error", "error": f"{type(exc).__name__}: {exc}"})


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


def _fmt_inputs(inputs: dict) -> str:
    """One-line summary of tool inputs for display."""
    parts = [f"{k}={v!r}" for k, v in inputs.items()]
    s = ", ".join(parts)
    return (s[:72] + "…") if len(s) > 72 else s


def run_agent(query: str, amadeus: AmadeusClient) -> None:
    """
    Run the Claude agent loop, executing tools as requested.

    Uses adaptive thinking and prompt caching on the system prompt.
    Displays a spinner while waiting for Claude and logs each tool call.
    """
    client = anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": query}]

    # System prompt with cache hint — saves tokens on repeated calls
    system_blocks = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    console.print(Rule("[bold blue]Analyzing your flight booking strategy[/bold blue]"))
    console.print()

    MAX_ITERATIONS = 12  # safety cap to avoid infinite loops
    iteration = 0
    response = None  # will be set in the loop

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        console=console,
        transient=True,
    ) as progress:

        while iteration < MAX_ITERATIONS:
            iteration += 1
            task = progress.add_task(f"Claude thinking… (step {iteration})", total=None)

            try:
                response = client.messages.create(
                    model="claude-opus-4-7",
                    max_tokens=8096,
                    thinking={"type": "adaptive"},
                    system=system_blocks,
                    tools=TOOLS,
                    messages=messages,
                )
            except anthropic.RateLimitError:
                progress.remove_task(task)
                console.print("[yellow]Claude API rate limited — waiting 30s…[/yellow]")
                time.sleep(30)
                continue
            except anthropic.APIConnectionError as exc:
                progress.remove_task(task)
                console.print(f"[red]Connection error:[/red] {exc}")
                return
            except anthropic.APIStatusError as exc:
                progress.remove_task(task)
                console.print(f"[red]Claude API error {exc.status_code}:[/red] {exc.message}")
                return

            progress.remove_task(task)

            # Append assistant turn
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    # Log the tool call
                    console.print(
                        f"  [dim]→[/dim] [bold yellow]{block.name}[/bold yellow]"
                        f"([dim]{_fmt_inputs(block.input)}[/dim])"
                    )

                    tool_task = progress.add_task(
                        f"Running {block.name}…", total=None
                    )
                    result_str = execute_tool(block.name, block.input, amadeus)
                    progress.remove_task(tool_task)

                    # Show result status
                    try:
                        result_obj = json.loads(result_str)
                        status = result_obj.get("status", "ok")
                    except (json.JSONDecodeError, AttributeError):
                        status = "ok"
                    status_color = {
                        "ok": "green",
                        "no_data": "yellow",
                        "partial": "yellow",
                        "rate_limited": "red",
                        "api_error": "red",
                        "unexpected_error": "red",
                    }.get(status, "dim")
                    console.print(f"    [dim]status:[/dim] [{status_color}]{status}[/{status_color}]")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

                messages.append({"role": "user", "content": tool_results})
                continue

            console.print(f"[yellow]Unexpected stop_reason: {response.stop_reason}[/yellow]")
            break

    if iteration >= MAX_ITERATIONS:
        console.print("[yellow]Warning: reached iteration limit — agent stopped early.[/yellow]")

    # Display final recommendation
    if response is None:
        console.print("[red]No response from agent.[/red]")
        return

    final_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            final_text += block.text

    if final_text:
        console.print()
        console.print(
            Panel(
                Markdown(final_text),
                title="[bold green]✈ Booking Strategy Recommendation[/bold green]",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        console.print("[yellow]Agent returned no text in its final response.[/yellow]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def resolve_city_to_iata(name: str) -> str:
    """Resolve a city/country name or IATA code to an uppercase IATA code."""
    key = name.strip().lower()
    return COUNTRY_TO_MAIN_AIRPORT.get(key, name.upper())


def build_query(origin: str, destination: str, month: str) -> str:
    """Build a structured natural-language query for the agent."""
    origin_iata = resolve_city_to_iata(origin)
    dest_iata = resolve_city_to_iata(destination)
    origin_city = IATA_TO_CITY.get(origin_iata, origin_iata)
    dest_city = IATA_TO_CITY.get(dest_iata, dest_iata)

    try:
        month_display = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    except ValueError:
        month_display = month

    return (
        f"I'm planning a sabbatical trip from {origin_city} ({origin_iata}) to "
        f"{dest_city} ({dest_iata}) in {month_display}.\n\n"
        f"Please give me a complete flight booking timing strategy:\n"
        f"1. Are prices for this route in {month_display} cheap or expensive historically? "
        f"(use get_price_insights for a couple of dates in that month)\n"
        f"2. What is a good current price snapshot? (use search_flight_prices)\n"
        f"3. How far in advance should I book to get the best price? "
        f"(use analyze_booking_patterns for {month})\n"
        f"4. Which days of the week are cheapest to fly? "
        f"(use get_cheapest_days_to_fly for {month})\n"
        f"5. Synthesize everything into a Booking Strategy with a confidence level.\n\n"
        f"Please call all four tools to gather real data before advising."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Flight Booking Timing Advisor — Sabbatical Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python flight_advisor.py
  python flight_advisor.py --origin SIN --destination BKK --month 2025-08
  python flight_advisor.py --origin BOM --destination KUL --month 2025-10
  python flight_advisor.py --query "When should I book SIN to Bali for July 2025?"
  python flight_advisor.py --origin DEL --destination SGN --month 2025-11 --currency USD
""",
    )
    parser.add_argument("--origin", help="Origin city/country or IATA code (e.g. SIN, Mumbai)")
    parser.add_argument("--destination", help="Destination city/country or IATA code (e.g. BKK, Bali)")
    parser.add_argument("--month", help="Target travel month in YYYY-MM format (e.g. 2025-08)")
    parser.add_argument("--query", help="Free-form query sent directly to the agent")
    parser.add_argument("--currency", default="SGD", help="Currency code for prices (default: SGD)")
    args = parser.parse_args()

    # --- Validate environment ---
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    amadeus_key = os.getenv("AMADEUS_API_KEY")
    amadeus_secret = os.getenv("AMADEUS_API_SECRET")

    missing = [k for k, v in [
        ("ANTHROPIC_API_KEY", anthropic_key),
        ("AMADEUS_API_KEY", amadeus_key),
        ("AMADEUS_API_SECRET", amadeus_secret),
    ] if not v]

    if missing:
        console.print(
            Panel(
                "[bold red]Missing environment variables:[/bold red]\n"
                + "\n".join(f"  • {k}" for k in missing)
                + "\n\n[dim]Copy [bold].env.example[/bold] to [bold].env[/bold] and fill in your keys.[/dim]\n\n"
                "• Anthropic keys: [link=https://console.anthropic.com]console.anthropic.com[/link]\n"
                "• Amadeus keys (free): [link=https://developers.amadeus.com]developers.amadeus.com[/link]",
                title="[red]Setup Required[/red]",
                border_style="red",
            )
        )
        sys.exit(1)

    amadeus = AmadeusClient(amadeus_key, amadeus_secret)

    # --- Banner ---
    console.print(
        Panel(
            "[bold]Flight Booking Timing Advisor[/bold] — Sabbatical Edition\n"
            "[dim]Routes: Singapore & India → Southeast Asia[/dim]\n\n"
            "Answers [bold green]WHEN[/bold green] to book — not just what flights exist.\n"
            "Uses real Amadeus price data + Claude AI analysis.",
            title="✈  Flight Advisor",
            border_style="blue",
            padding=(0, 2),
        )
    )
    console.print()

    # --- Determine query ---
    if args.query:
        query = args.query
    elif args.origin and args.destination and args.month:
        origin_iata = resolve_city_to_iata(args.origin)
        dest_iata = resolve_city_to_iata(args.destination)
        query = build_query(origin_iata, dest_iata, args.month)
    else:
        # Interactive mode
        console.print("[bold]Common origins:[/bold]  SIN · BOM · DEL · MAA · BLR · CCU · HYD")
        console.print("[bold]Common destinations:[/bold]  BKK · KUL · DPS · SGN · HAN · MNL · RGN · CMB")
        console.print()

        raw_origin = Prompt.ask("[bold]Origin[/bold] (city name or IATA code)").strip()
        raw_dest = Prompt.ask("[bold]Destination[/bold] (city name or IATA code)").strip()

        origin_iata = resolve_city_to_iata(raw_origin)
        dest_iata = resolve_city_to_iata(raw_dest)

        if origin_iata != raw_origin.upper():
            console.print(f"[dim]→ Using {origin_iata} ({IATA_TO_CITY.get(origin_iata, origin_iata)})[/dim]")
        if dest_iata != raw_dest.upper():
            console.print(f"[dim]→ Using {dest_iata} ({IATA_TO_CITY.get(dest_iata, dest_iata)})[/dim]")

        default_month = (datetime.now() + timedelta(days=60)).strftime("%Y-%m")
        month = Prompt.ask(
            "[bold]Travel month[/bold] (YYYY-MM)", default=default_month
        ).strip()

        try:
            datetime.strptime(month + "-01", "%Y-%m-%d")
        except ValueError:
            console.print(f"[red]Invalid month: {month!r}. Use YYYY-MM format.[/red]")
            sys.exit(1)

        query = build_query(origin_iata, dest_iata, month)

    console.print(f"[dim]Query:[/dim] {query[:120]}…\n" if len(query) > 120 else f"[dim]Query:[/dim] {query}\n")
    run_agent(query, amadeus)


if __name__ == "__main__":
    main()
