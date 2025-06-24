from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, db
import requests
from datetime import datetime
import paho.mqtt.client as mqtt
import json
import logging
import os
import pytz
import uuid

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

tz = pytz.timezone("Africa/Cairo")

app = Flask(__name__)
CORS(app)

# Initialize Firebase Realtime Database
if not firebase_admin._apps:
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        cred = credentials.ApplicationDefault()
    else:
        cred = credentials.Certificate("telematics-database-firebase-adminsdk-fbsvc-e727.json")
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://telematics-database-default-rtdb.firebaseio.com/'
    })
db_ref = db.reference()

SERPAPI_KEY = "aa1a8a1b1b1b9151a178be3d041632b3d670504600f71ed941634bc1b23dfe79"
MQTT_BROKER = "test.mosquitto.org"
MQTT_PORT = 1883
MQTT_TOPIC_ACCIDENT = "accident/data"

mqtt_client = mqtt.Client(client_id="flask_app", protocol=mqtt.MQTTv311, callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

def search_nearest_hospitals(latitude, longitude):
    search_url = "https://serpapi.com/search"
    params = {
        "engine": "google_maps",
        "type": "search",
        "q": "hospitals",
        "ll": f"@{latitude},{longitude},15z",
        "key": SERPAPI_KEY
    }
    try:
        response = requests.get(search_url, params=params)
        response.raise_for_status()
        data = response.json()
        if "local_results" in data and data["local_results"]:
            hospitals = []
            for result in data["local_results"]:
                if "type" in result and "hospital" in result["type"].lower():
                    hospital_data = {
                        "name": result.get("title"),
                        "address": result.get("address"),
                        "phone": result.get("phone"),
                        "latitude": result.get("gps_coordinates", {}).get("latitude", None),
                        "longitude": result.get("gps_coordinates", {}).get("longitude", None)
                    }
                    hospitals.append(hospital_data)
                    if len(hospitals) == 3:
                        break
            return hospitals
        else:
            logger.warning("No local results found in SerpAPI response")
            return []
    except Exception as e:
        logger.error(f"Error searching for hospitals: {e}")
        return []

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        logger.info("Connected to MQTT broker successfully")
        client.subscribe(MQTT_TOPIC_ACCIDENT, qos=1)
        logger.info(f"Subscribed to topic: {MQTT_TOPIC_ACCIDENT}")
    else:
        logger.error(f"Failed to connect to MQTT broker. Reason code: {reason_code}")

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode().strip()
        if not payload:
            logger.warning("Empty payload received")
            return

        data = json.loads(payload)
        if not data or "car_id" not in data or "latitude" not in data or "longitude" not in data:
            logger.warning("Invalid accident data received")
            return

        try:
            latitude = float(data['latitude'])
            longitude = float(data['longitude'])
        except (ValueError, KeyError) as e:
            logger.error(f"Error parsing latitude/longitude: {e}")
            return

        if "timestamp" in data:
            try:
                timestamp = datetime.strptime(data["timestamp"], "%Y-%m-%dT%H:%M:%S.%fZ")
            except ValueError:
                timestamp = datetime.now(tz)
        else:
            timestamp = datetime.now(tz)

        message_type = data.get("Type", "Accident")
        show = data.get("show", "Yes")

        hospitals = search_nearest_hospitals(latitude, longitude)
        hospital = hospitals[0] if hospitals else None
        hospital_latitude = hospital.get("latitude") if hospital else None
        hospital_longitude = hospital.get("longitude") if hospital else None

        doc_data = {
            "car_id": data['car_id'],
            "latitude": latitude,
            "longitude": longitude,
            "timestamp": timestamp.isoformat(),
            "nearest_hospital": hospital["name"] if hospital else "Not found",
            "hospital_address": hospital["address"] if hospital else "Not found",
            "hospital_phone": hospital["phone"] if hospital else "Not found",
            "hospital_latitude": hospital_latitude,
            "hospital_longitude": hospital_longitude,
            "show": show,
            "Type": message_type  # Added to store Type in database
        }

        record_id = str(uuid.uuid4())

        if message_type == "Accident":
            db_ref.child("accidents").child(record_id).set(doc_data)
            logger.info("Accident data saved to Realtime Database")
        elif message_type == "Emergency":
            db_ref.child("emergencies").child(record_id).set(doc_data)
            logger.info("Emergency data saved to Realtime Database")

    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON: {e}")
    except Exception as e:
        logger.error(f"Error processing MQTT message: {e}")

if not os.environ.get("WERKZEUG_RUN_MAIN"):
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()

@app.route('/')
def index():
    accidents = db_ref.child("accidents").order_by_child("show").equal_to("Yes").get() or {}
    emergencies = db_ref.child("emergencies").order_by_child("show").equal_to("Yes").get() or {}
    all_records = []

    for key, value in accidents.items():
        value["id"] = key
        value["timestamp"] = datetime.fromisoformat(value["timestamp"])
        all_records.append(value)
    for key, value in emergencies.items():
        value["id"] = key
        value["timestamp"] = datetime.fromisoformat(value["timestamp"])
        all_records.append(value)

    all_records.sort(key=lambda x: x["timestamp"], reverse=True)
    return render_template('index.html', all_accidents=all_records)

@app.route('/accidents')
def accidents_only():
    accidents = db_ref.child("accidents").order_by_child("show").equal_to("Yes").get() or {}
    records = [{"id": key, **value, "timestamp": datetime.fromisoformat(value["timestamp"])} for key, value in accidents.items()]
    return render_template('index.html', all_accidents=records)

@app.route('/emergencies')
def emergencies_only():
    emergencies = db_ref.child("emergencies").order_by_child("show").equal_to("Yes").get() or {}
    records = [{"id": key, **value, "timestamp": datetime.fromisoformat(value["timestamp"])} for key, value in emergencies.items()]
    return render_template('index.html', all_accidents=records)

@app.route('/api/nearest_hospitals')
def get_nearest_hospitals():
    latitude = request.args.get('latitude', type=float)
    longitude = request.args.get('longitude', type=float)
    if latitude is None or longitude is None:
        return jsonify({"error": "Latitude and longitude parameters are required"}), 400
    hospitals = search_nearest_hospitals(latitude, longitude)
    return jsonify(hospitals)


if __name__ == "__main__":
    import os
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))