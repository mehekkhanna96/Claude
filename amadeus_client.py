"""
Amadeus API client wrapper for flight price data and analytics.

Handles OAuth2 authentication, flight search, price insights, and
cheapest date analysis using the Amadeus test API.
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

AMADEUS_BASE_URL = "https://test.api.amadeus.com"


class AmadeusRateLimitError(Exception):
    """Raised when Amadeus API returns 429 Too Many Requests."""
    pass


class AmadeusAPIError(Exception):
    """Raised when Amadeus API returns an unexpected error."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Amadeus API error {status_code}: {message}")


class AmadeusClient:
    """
    Client for the Amadeus travel APIs.

    Handles token caching, automatic refresh, and rate-limit detection.
    """

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def get_access_token(self) -> str:
        """
        Return a valid Bearer token, fetching a new one if expired.

        Uses client_credentials OAuth2 flow.
        """
        now = time.time()
        if self._access_token and now < self._token_expires_at - 30:
            return self._access_token

        resp = self._session.post(
            f"{AMADEUS_BASE_URL}/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.api_key,
                "client_secret": self.api_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )

        if resp.status_code == 429:
            raise AmadeusRateLimitError("Rate limited while fetching token")
        resp.raise_for_status()

        payload = resp.json()
        self._access_token = payload["access_token"]
        self._token_expires_at = now + int(payload.get("expires_in", 1799))
        return self._access_token

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.get_access_token()}"}

    def _get(self, path: str, params: dict) -> dict:
        """
        Execute an authenticated GET request and return JSON payload.

        Raises AmadeusRateLimitError on 429, AmadeusAPIError on other
        non-2xx responses.
        """
        resp = self._session.get(
            f"{AMADEUS_BASE_URL}{path}",
            headers=self._auth_headers(),
            params=params,
            timeout=20,
        )

        if resp.status_code == 429:
            raise AmadeusRateLimitError(
                "Amadeus API rate limit reached. Please wait a moment and retry."
            )
        if not resp.ok:
            try:
                detail = resp.json().get("errors", [{}])[0].get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise AmadeusAPIError(resp.status_code, detail)

        return resp.json()

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

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
        """
        Search for current flight offers.

        Args:
            origin: IATA origin airport code (e.g. "SIN").
            destination: IATA destination airport code (e.g. "BKK").
            departure_date: Date string in YYYY-MM-DD format.
            return_date: Optional return date for round trips.
            adults: Number of adult passengers.
            currency: Currency code for prices.
            max_results: Maximum number of offers to return (max 5 for free tier).

        Returns:
            List of flight offer dicts with keys:
                price, currency, airline, stops, departure, arrival, duration.
        """
        params = {
            "originLocationCode": origin.upper(),
            "destinationLocationCode": destination.upper(),
            "departureDate": departure_date,
            "adults": adults,
            "max": max_results,
            "currencyCode": currency,
        }
        if return_date:
            params["returnDate"] = return_date

        try:
            data = self._get("/v2/shopping/flight-offers", params)
        except AmadeusAPIError as exc:
            logger.warning("Flight search failed: %s", exc)
            return []

        offers = []
        for offer in data.get("data", [])[:max_results]:
            try:
                price = float(offer["price"]["grandTotal"])
                carrier_codes = list(
                    {
                        seg["carrierCode"]
                        for itin in offer["itineraries"]
                        for seg in itin["segments"]
                    }
                )
                total_stops = sum(
                    len(itin["segments"]) - 1
                    for itin in offer["itineraries"]
                )
                first_seg = offer["itineraries"][0]["segments"][0]
                last_seg = offer["itineraries"][0]["segments"][-1]
                duration = offer["itineraries"][0].get("duration", "PT?H?M")

                offers.append(
                    {
                        "price": price,
                        "currency": currency,
                        "airline": ", ".join(carrier_codes),
                        "stops": total_stops,
                        "departure": first_seg["departure"].get("at", ""),
                        "arrival": last_seg["arrival"].get("at", ""),
                        "duration": duration,
                        "origin": origin.upper(),
                        "destination": destination.upper(),
                        "departure_date": departure_date,
                    }
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("Skipping malformed offer: %s", exc)
                continue

        return sorted(offers, key=lambda o: o["price"])

    def get_price_insights(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        currency: str = "SGD",
        one_way: bool = True,
    ) -> dict:
        """
        Fetch historical price percentiles for a route/date.

        Uses the Amadeus Itinerary Price Metrics endpoint:
        /v1/analytics/itinerary-price-metrics

        Args:
            origin: IATA origin code.
            destination: IATA destination code.
            departure_date: Date in YYYY-MM-DD format.
            currency: Currency code.
            one_way: True for one-way price analysis.

        Returns:
            Dict with keys:
                min, first_quartile, median, third_quartile, max (prices),
                origin, destination, departure_date, currency.
            Returns empty dict if data unavailable.
        """
        params = {
            "originIataCode": origin.upper(),
            "destinationIataCode": destination.upper(),
            "departureDate": departure_date,
            "currencyCode": currency,
            "oneWay": str(one_way).lower(),
        }

        try:
            data = self._get("/v1/analytics/itinerary-price-metrics", params)
        except AmadeusAPIError as exc:
            logger.info(
                "Price metrics unavailable for %s→%s on %s: %s",
                origin,
                destination,
                departure_date,
                exc,
            )
            return {}

        metrics_list = data.get("data", [])
        if not metrics_list:
            return {}

        # The endpoint can return multiple records; use the first.
        metrics = metrics_list[0].get("priceMetrics", [])
        price_map: dict[str, Optional[float]] = {
            "MINIMUM": None,
            "FIRST": None,
            "MEDIUM": None,
            "THIRD": None,
            "MAXIMUM": None,
        }
        for m in metrics:
            quartile = m.get("quartileRanking", "").upper()
            try:
                price_map[quartile] = float(m["amount"])
            except (KeyError, ValueError, TypeError):
                pass

        return {
            "min": price_map["MINIMUM"],
            "first_quartile": price_map["FIRST"],
            "median": price_map["MEDIUM"],
            "third_quartile": price_map["THIRD"],
            "max": price_map["MAXIMUM"],
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
        """
        Search flight prices across multiple departure dates to find cheapest days.

        Samples every day in the window (up to duration_days).  Returns price
        data per departure date so patterns can be identified.

        Args:
            origin: IATA origin code.
            destination: IATA destination code.
            departure_date_range_start: Start of search window (YYYY-MM-DD).
            duration_days: Number of calendar days to scan.
            currency: Currency code.

        Returns:
            List of dicts sorted by price:
                date, price, currency, airline, stops, day_of_week.
        """
        start = datetime.strptime(departure_date_range_start, "%Y-%m-%d")
        results: list[dict] = []

        for offset in range(duration_days):
            check_date = (start + timedelta(days=offset)).strftime("%Y-%m-%d")
            try:
                offers = self.search_flight_prices(
                    origin=origin,
                    destination=destination,
                    departure_date=check_date,
                    currency=currency,
                    max_results=1,
                )
                if offers:
                    best = offers[0]
                    day_name = (start + timedelta(days=offset)).strftime("%A")
                    results.append(
                        {
                            "date": check_date,
                            "price": best["price"],
                            "currency": currency,
                            "airline": best["airline"],
                            "stops": best["stops"],
                            "day_of_week": day_name,
                        }
                    )
                # Brief pause to be kind to rate limits
                time.sleep(0.2)
            except AmadeusRateLimitError:
                logger.warning("Rate limited during date scan at %s", check_date)
                break
            except AmadeusAPIError as exc:
                logger.debug("No data for %s: %s", check_date, exc)
                continue

        return sorted(results, key=lambda r: r["price"])
