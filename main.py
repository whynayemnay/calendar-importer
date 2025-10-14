import os
import threading
import time
import json
from datetime import datetime, timedelta
import pytz


import requests
from dotenv import load_dotenv
from ics import Calendar, Event
from flask import Flask, Response, jsonify, request

app = Flask(__name__)
load_dotenv()

# ===== CONFIG =====
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
# ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
STRAVA_VERIFY_TOKEN = os.getenv("STRAVA_VERIFY_TOKEN", "strava_secret_token")
SUBSCRIPTION_ID = os.getenv("SUBSCRIPTION_ID")
TOKEN_FILE = "token.json"
PER_PAGE = 20

local_tz = pytz.timezone("Europe/Berlin")


def load_tokens():
    with open(TOKEN_FILE, "r") as f:
        return json.load(f)


def save_tokens(tokens):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


def refresh_strava_webhook(callback_url: str):
    ### Delete current sub, should be only 1 but in case more
    resp = requests.get(
        "https://www.strava.com/api/v3/push_subscriptions",
        params={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
    )
    resp.raise_for_status()
    subs = resp.json()

    for sub in subs:
        sub_id = sub["id"]
        del_resp = requests.delete(
            f"https://www.strava.com/api/v3/push_subscriptions/{sub_id}",
            params={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        )
        if del_resp.status_code == 204:
            print(f"[x] Deleted subscritption {sub_id}")
        else:
            print(f"!!! Failed to delete {sub_id}: {del_resp.text}")

    create_resp = requests.post(
        "https://www.strava.com/api/v3/push_subscriptions",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "callback_url": callback_url,
            "verify_token": STRAVA_VERIFY_TOKEN,
        },
    )
    if create_resp.status_code == 201:
        sub_info = create_resp.json()
        print(f"[x] Created new subscription: {sub_info}")
        return sub_info
    else:
        raise Exception(f"Failed to create subscription: {create_resp.text}")


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


def fetch_strava_activity(activity_id: int):
    access_token = get_valid_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://www.strava.com/api/v3/activities/{activity_id}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


# ===== BUILD CALENDAR =====
def build_calendar_file(activities, calendar_file="calendar.ics"):
    calendar = Calendar()

    for activity in activities:
        event = Event()
        event.uid = str(activity["id"])
        event.name = activity["name"]

        start_time = datetime.fromisoformat(
            activity["start_date"].replace("Z", "+00:00")
        )
        duration = timedelta(seconds=activity["elapsed_time"])
        event.begin = start_time
        event.end = start_time + duration

        event.description = (
            f"Type: {activity.get('type')} | Sport: {activity.get('sport_type')}"
        )

        # Avoid duplicates (mostly for safety)
        if not any(ev.uid == event.uid for ev in calendar.events):
            calendar.events.add(event)

    with open(calendar_file, "w") as f:
        f.write(calendar.serialize())


def add_sleep_to_calendar(
    start_dt, end_dt, description, calendar_file="sleep_calendar.ics"
):
    if os.path.exists(calendar_file):
        with open(calendar_file, "r") as f:
            calendar = Calendar(f.read())
    else:
        calendar = Calendar()

    event = Event()
    event.uid = f"sleep-{start_dt.isoformat()}"
    event.name = "Sleep"
    event.begin = start_dt
    event.end = end_dt
    event.description = description

    # Avoid duplicates
    if not any(ev.uid == event.uid for ev in calendar.events):
        calendar.events.add(event)

    with open(calendar_file, "w") as f:
        f.write(calendar.serialize())


def add_activity_to_calendar(activity, calendar_file="calendar.ics"):
    if os.path.exists(calendar_file):
        with open(calendar_file, "r") as f:
            calendar = Calendar(f.read())
    else:
        calendar = Calendar()

    event = Event()
    event.uid = str(activity["id"])
    event.name = activity["name"]

    start_time = datetime.fromisoformat(activity["start_date"].replace("Z", "+00:00"))
    duration = timedelta(seconds=activity["elapsed_time"])
    end_time = start_time + duration

    event.begin = start_time
    event.end = end_time
    event.description = (
        f"Type: {activity.get('type')} | Sport: {activity.get('sport_type')}"
    )

    # Avoid duplicates (in case webhook fires twice for same activity)
    if not any(ev.uid == event.uid for ev in calendar.events):
        calendar.events.add(event)

    # Save back to file
    with open(calendar_file, "w") as f:
        f.write(calendar.serialize())


@app.route("/strava/webhook", methods=["GET", "POST"])
def strava_webhook():
    if request.method == "GET":
        # Verification challenge
        verify_token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if verify_token == STRAVA_VERIFY_TOKEN:
            return jsonify({"hub.challenge": challenge})
        else:
            return "invalid verify token", 403

    if request.method == "POST":
        data = request.json
        if not data:
            return jsonify({"error", "no JSON payload"}), 400

        # Basic validation
        if SUBSCRIPTION_ID and str(data.get("subscription_id")) != SUBSCRIPTION_ID:
            return "invalid subscription_id", 403

        # (Optional) Only accept events from your athlete ID
        # if str(data.get("owner_id")) != os.getenv("STRAVA_ATHLETE_ID"):
        #     return "Invalid owner", 403
        object_id = data.get("object_id")

        # Kick off background work (don’t block response)
        if (
            data.get("object_type") == "activity"
            and data.get("aspect_type") == "create"
        ):
            threading.Thread(
                target=lambda: (
                    print(f"processing activity {object_id}"),
                    add_activity_to_calendar(fetch_strava_activity(object_id)),
                    print(f"processed {object_id}"),
                )
            ).start()
            print("received webhook event:", data)

        # TODO:        # get a domain instead of using cloudflare quick tunnel
        return "", 200
    return "", 405


@app.route("/calendar.ics")
def calendar_feed():
    if not os.path.exists("calendar.ics"):
        return "Calendar not built yet", 404
    with open("calendar.ics", "r") as f:
        return Response(f.read(), mimetype="text/calendar")


@app.route("/rebuild-calendar", methods=["POST"])
def rebuild_calendar():
    activities = fetch_strava_activities()
    build_calendar_file(activities)
    return "Calendar rebuilt", 200


@app.route("/refresh-webhook", methods=["POST"])
def refresh_webhook_route():
    data = request.json
    if not data or "callback_url" not in data:
        return jsonify({"error": "Missing 'callback_url' in JSON body"}), 400

    callback_url = data["callback_url"].rstrip("/") + "/strava/webhook"
    try:
        new_sub = refresh_strava_webhook(callback_url)
        return jsonify({"message": "Subscription refreshed", "subscription": new_sub})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/sleep", methods=["POST"])
def sleep_data():
    data = request.json
    required_keys = {"startdate", "enddate"}
    if not data or not required_keys.issubset(data.keys()):
        return jsonify({"error": "missing 'startdate' or 'enddate' in JSON body"}), 400

    print("sleep data:", data)

    try:
        start_str = data["startdate"]
        end_str = data["enddate"]
        description = data.get("description", "Sleep")

        start_dt = datetime.strptime(start_str, "%d.%m.%Y, %H:%M")
        end_dt = datetime.strptime(end_str, "%d.%m.%Y, %H:%M")

        start_dt = local_tz.localize(start_dt)
        end_dt = local_tz.localize(end_dt)

        add_sleep_to_calendar(start_dt, end_dt, description)
        return "Sleep event added", 200

    except Exception as e:
        print("Error processing sleep data:", e)
        return jsonify({"error": str(e)}), 500


@app.route("/sleep_calendar.ics")
def sleep_calendar_feed():
    if not os.path.exists("sleep_calendar.ics"):
        return "Sleep calendar not built yet", 404
    with open("sleep_calendar.ics", "r") as f:
        return Response(f.read(), mimetype="text/calendar")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
