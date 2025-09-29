import os
import time
import json
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv
from ics import Calendar, Event
from flask import Flask, Response

app = Flask(__name__)
load_dotenv()

# ===== CONFIG =====
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
# ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
TOKEN_FILE = "token.json"
PER_PAGE = 10

# TODO: implement refresh token logic


def load_tokens():
    with open(TOKEN_FILE, "r") as f:
        return json.load(f)


def save_tokens(tokens):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


def get_valid_access_token():
    tokens = load_tokens()
    now = int(time.time())
    if now >= tokens["expires_at"]:
        print("access token expired, refreshing...")
        response = requests.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
            },
        )
        response.raise_for_status()
        new_tokens = response.json()
        # update JSON with new tokens
        tokens["access_token"] = new_tokens["access_token"]
        tokens["refresh_token"] = new_tokens["refresh_token"]
        tokens["expires_at"] = new_tokens["expires_at"]
        save_tokens(tokens)
    return tokens["access_token"]


# ===== FETCH STRAVA ACTIVITIES =====
def fetch_strava_activities():
    access_token = get_valid_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://www.strava.com/api/v3/athlete/activities?per_page={PER_PAGE}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


# ===== BUILD CALENDAR =====
def build_calendar(activities):
    calendar = Calendar()
    for activity in activities:
        event = Event()
        event.uid = str(activity["id"])
        event.name = activity["name"]
        # Start and end times
        start_time = datetime.fromisoformat(
            activity["start_date"].replace("Z", "+00:00")
        )
        duration = timedelta(seconds=activity["elapsed_time"])
        end_time = start_time + duration

        event.begin = start_time
        event.end = end_time
        # Optional description
        event.description = (
            f"Type: {activity.get('type')} | Sport: {activity.get('sport_type')}"
        )
        # # Optional location
        # loc_parts = filter(
        #     None,
        #     [
        #         activity.get("location_city"),
        #         activity.get("location_state"),
        #         activity.get("location_country"),
        #     ],
        # )
        # event.location = ", ".join(loc_parts) if loc_parts else ""
        #
        calendar.events.add(event)
    return calendar


@app.route("/calendar.ics")
def calendar_feed():
    activities = fetch_strava_activities()
    calendar = build_calendar(activities)
    return Response(calendar.serialize(), mimetype="text/calendar")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
