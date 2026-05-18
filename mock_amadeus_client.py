"""
Mock Amadeus client for testing without API credentials.

Returns realistic-looking price data for common SIN/India → SEA routes.
Use by passing --mock to flight_advisor.py.
"""

import random
from datetime import datetime, timedelta
from typing import Optional


# Realistic base prices (SGD) for common routes
BASE_PRICES = {
    ("SIN", "BKK"): 120,
    ("SIN", "KUL"): 80,
    ("SIN", "DPS"): 200,
    ("SIN", "CGK"): 160,
    ("SIN", "SGN"): 150,
    ("SIN", "HAN"): 180,
    ("SIN", "MNL"): 220,
    ("SIN", "RGN"): 250,
    ("SIN", "PNH"): 280,
    ("SIN", "REP"): 300,
    ("SIN", "CMB"): 260,
    ("BOM", "BKK"): 380,
    ("BOM", "KUL"): 320,
    ("BOM", "DPS"): 480,
    ("BOM", "SGN"): 420,
    ("DEL", "BKK"): 350,
    ("DEL", "KUL"): 300,
    ("DEL", "DPS"): 460,
    ("MAA", "BKK"): 360,
    ("MAA", "KUL"): 280,
    ("BLR", "BKK"): 370,
    ("BLR", "KUL"): 290,
    ("HYD", "BKK"): 380,
    ("CCU", "BKK"): 340,
}

AIRLINES = {
    ("SIN", "BKK"): ["SQ", "TG", "TR", "FD"],
    ("SIN", "KUL"): ["SQ", "AK", "MH"],
    ("SIN", "DPS"): ["SQ", "TR", "JT"],
    ("SIN", "SGN"): ["SQ", "VN", "TR"],
    ("SIN", "HAN"): ["SQ", "VN"],
    ("SIN", "MNL"): ["SQ", "PR", "5J"],
    ("BOM", "BKK"): ["AI", "TG", "SQ"],
    ("DEL", "BKK"): ["AI", "TG", "SQ"],
}

CURRENCY_RATES = {
    "SGD": 1.0,
    "USD": 0.74,
    "INR": 61.5,
    "MYR": 3.35,
    "THB": 27.0,
}


def _base_price(origin: str, destination: str) -> float:
    key = (origin.upper(), destination.upper())
    rev = (destination.upper(), origin.upper())
    return BASE_PRICES.get(key) or BASE_PRICES.get(rev) or 300.0


def _convert(sgd_price: float, currency: str) -> float:
    rate = CURRENCY_RATES.get(currency.upper(), 1.0)
    return round(sgd_price * rate, 2)


def _day_factor(date_str: str) -> float:
    """Friday/Saturday cost ~15% more; Monday/Tuesday ~8% cheaper."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return 1.0
    factors = {0: 0.92, 1: 0.95, 2: 1.0, 3: 1.0, 4: 1.10, 5: 1.15, 6: 1.08}
    return factors[dt.weekday()]


def _lead_factor(date_str: str) -> float:
    """Prices rise sharply inside 14 days, drop a bit at 45–60 days."""
    try:
        days_ahead = (datetime.strptime(date_str, "%Y-%m-%d") - datetime.now()).days
    except ValueError:
        return 1.0
    if days_ahead <= 0:
        return 1.5
    if days_ahead <= 7:
        return 1.4
    if days_ahead <= 14:
        return 1.25
    if days_ahead <= 21:
        return 1.12
    if days_ahead <= 30:
        return 1.05
    if days_ahead <= 45:
        return 0.97
    if days_ahead <= 60:
        return 0.95
    if days_ahead <= 90:
        return 1.0
    return 1.05


def _peak_factor(date_str: str) -> float:
    """Dec 15–Jan 5 and Jun 1–Jul 15 are peak season (+20–30%)."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return 1.0
    m, d = dt.month, dt.day
    if (m == 12 and d >= 15) or (m == 1 and d <= 5):
        return 1.28
    if m == 6 or (m == 7 and d <= 15):
        return 1.18
    if m in (2, 3, 9, 10):
        return 0.90  # shoulder season — cheaper
    return 1.0


class MockAmadeusClient:
    """
    Drop-in replacement for AmadeusClient that returns synthetic but
    realistic price data.  No network calls, no credentials required.
    """

    def __init__(self):
        pass

    def search_flight_prices(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: Optional[str] = None,
        adults: int = 1,
        currency: str = "SGD",
        max_results: int = 5,
    ) -> list[dict]:
        base = _base_price(origin, destination)
        factor = _day_factor(departure_date) * _lead_factor(departure_date) * _peak_factor(departure_date)
        rng = random.Random(departure_date + origin + destination)

        key = (origin.upper(), destination.upper())
        rev = (destination.upper(), origin.upper())
        airlines = AIRLINES.get(key) or AIRLINES.get(rev) or ["XX", "YY"]

        offers = []
        n = min(max_results, len(airlines) + 1)
        for i in range(n):
            noise = rng.uniform(0.93, 1.15) if i == 0 else rng.uniform(1.05, 1.35)
            price = _convert(base * factor * noise, currency)
            airline = airlines[i % len(airlines)]
            stops = 0 if i == 0 else rng.choice([1, 1, 2])
            dep_hour = rng.choice([6, 8, 10, 14, 18, 21])
            dep_dt = departure_date + f"T{dep_hour:02d}:00:00"
            arr_hour = (dep_hour + 2 + stops * 2) % 24
            arr_dt = departure_date + f"T{arr_hour:02d}:30:00"
            duration = f"PT{2 + stops * 2}H30M"
            offers.append({
                "price": price,
                "currency": currency,
                "airline": airline,
                "stops": stops,
                "departure": dep_dt,
                "arrival": arr_dt,
                "duration": duration,
                "origin": origin.upper(),
                "destination": destination.upper(),
                "departure_date": departure_date,
            })

        return sorted(offers, key=lambda o: o["price"])

    def get_price_insights(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        currency: str = "SGD",
        one_way: bool = True,
    ) -> dict:
        base = _base_price(origin, destination)
        pf = _peak_factor(departure_date)
        converted_base = _convert(base * pf, currency)

        mn = round(converted_base * 0.70, 2)
        q1 = round(converted_base * 0.85, 2)
        median = round(converted_base * 1.00, 2)
        q3 = round(converted_base * 1.20, 2)
        mx = round(converted_base * 1.60, 2)

        return {
            "min": mn,
            "first_quartile": q1,
            "median": median,
            "third_quartile": q3,
            "max": mx,
            "origin": origin.upper(),
            "destination": destination.upper(),
            "departure_date": departure_date,
            "currency": currency,
        }

    def get_cheapest_date_suggestions(
        self,
        origin: str,
        destination: str,
        departure_date_range_start: str,
        duration_days: int = 30,
        currency: str = "SGD",
    ) -> list[dict]:
        start = datetime.strptime(departure_date_range_start, "%Y-%m-%d")
        results = []
        for offset in range(duration_days):
            dt = start + timedelta(days=offset)
            date_str = dt.strftime("%Y-%m-%d")
            offers = self.search_flight_prices(
                origin=origin,
                destination=destination,
                departure_date=date_str,
                currency=currency,
                max_results=1,
            )
            if offers:
                results.append({
                    "date": date_str,
                    "price": offers[0]["price"],
                    "currency": currency,
                    "airline": offers[0]["airline"],
                    "stops": offers[0]["stops"],
                    "day_of_week": dt.strftime("%A"),
                })
        return sorted(results, key=lambda r: r["price"])
