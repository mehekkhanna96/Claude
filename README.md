# Flight Booking Timing Advisor

An AI agent that answers **when** to book flights, not just what's available. Uses historical price percentiles from Amadeus to recommend whether to buy now or wait, and identifies cheapest departure days and lead times for routes from Singapore and India to Southeast Asia.

## How It Works

The agent uses Claude (Opus 4.7 with adaptive thinking) plus four Amadeus-backed tools:

1. **`search_flight_prices`** — current live offers for a route/date
2. **`get_price_insights`** — historical price percentiles (min, 25th, median, 75th, max) so the agent can say "today's price is below the median — buy now"
3. **`analyze_booking_patterns`** — samples prices at 14/21/30/45/60/90 days lead time to find the sweet spot
4. **`get_cheapest_days_to_fly`** — scans an entire month to surface cheapest departure days and day-of-week patterns

## Setup

### 1. Get API Keys

**Anthropic:**
- Sign up at [console.anthropic.com](https://console.anthropic.com) and create an API key.

**Amadeus (free tier):**
1. Register at [developers.amadeus.com](https://developers.amadeus.com)
2. Create an app in the Self-Service portal
3. Copy your **API Key** and **API Secret** from the app dashboard

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

`.env` format:
```
ANTHROPIC_API_KEY=sk-ant-...
AMADEUS_API_KEY=...
AMADEUS_API_SECRET=...
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

## Usage

### Interactive Mode

Run without arguments to be prompted:

```bash
python flight_advisor.py
```

### CLI Arguments

```bash
python flight_advisor.py --origin SIN --destination BKK --month 2026-08
```

| Flag | Description | Example |
|---|---|---|
| `--origin` | IATA code or city name | `SIN`, `BOM`, `singapore` |
| `--destination` | IATA code or country name | `BKK`, `DPS`, `thailand` |
| `--month` | Month to analyze (YYYY-MM) | `2026-08` |

### Supported Origins

| Code | Airport |
|---|---|
| SIN | Singapore Changi |
| BOM | Mumbai Chhatrapati Shivaji |
| DEL | Delhi Indira Gandhi |
| MAA | Chennai |
| BLR | Bengaluru |
| CCU | Kolkata |
| HYD | Hyderabad |

### Supported Southeast Asia Destinations

| Code | Destination |
|---|---|
| BKK | Bangkok (Suvarnabhumi) |
| DMK | Bangkok (Don Mueang) |
| KUL | Kuala Lumpur |
| DPS | Bali (Denpasar) |
| CGK | Jakarta |
| SGN | Ho Chi Minh City |
| HAN | Hanoi |
| MNL | Manila |
| SIN | Singapore |
| RGN | Yangon |
| PNH | Phnom Penh |
| VTE | Vientiane |

You can also type country names: `thailand`, `bali`, `vietnam`, `malaysia`, `indonesia`, `philippines`, `myanmar`, `cambodia`, `laos`.

## Example Queries

```
What's the best time to book SIN → BKK for August 2026?
Should I book now or wait for cheaper fares to Bali in July?
Which days of the week are cheapest to fly BOM → BKK?
How far in advance should I book flights from Singapore to Vietnam?
```

## Notes

- Amadeus free tier has rate limits. The agent adds small delays between calls to stay within limits.
- Price metrics (historical percentiles) may not be available for all routes/dates; the agent falls back gracefully.
- All prices are in SGD by default.
