import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv
from ics import Calendar, Event

load_dotenv()

# ===== CONFIG =====
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
PER_PAGE = 10

# ===== FETCH STRAVA ACTIVITIES =====
headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
url = f"https://www.strava.com/api/v3/athlete/activities?per_page={PER_PAGE}"

response = requests.get(url, headers=headers)
activities = response.json()

# ===== CREATE ICS CALENDAR =====
calendar = Calendar()

for activity in activities:
    event = Event()

    # Event UID
    event.uid = str(activity["id"])

    # Title / summary
    event.name = activity["name"]

    # Start and end times
    start_time = datetime.fromisoformat(activity["start_date"].replace("Z", "+00:00"))
    duration = timedelta(seconds=activity["elapsed_time"])
    end_time = start_time + duration

    event.begin = start_time
    event.end = end_time

    # Optional description
    event.description = (
        f"Type: {activity.get('type')} | Sport: {activity.get('sport_type')}"
    )

    # Optional location
    loc_parts = filter(
        None,
        [
            activity.get("location_city"),
            activity.get("location_state"),
            activity.get("location_country"),
        ],
    )
    event.location = ", ".join(loc_parts) if loc_parts else ""

    calendar.events.add(event)

# ===== WRITE TO FILE =====
with open("strava_activities.ics", "w") as f:
    f.write(calendar.serialize())

print("ICS file created: strava_activities.ics")
