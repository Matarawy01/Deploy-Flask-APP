from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
import requests
from datetime import datetime
import paho.mqtt.client as mqtt
import json
import logging
import os
import pytz
import sqlite3

# Set up logging
logging.basicConfig(level=logging.INFO)  # Changed to INFO for production
logger = logging.getLogger(__name__)

tz = pytz.timezone("Africa/Cairo")

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///accidents.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Use environment variable for API key in production
SERPAPI_KEY = os.environ.get('SERPAPI_KEY', "aa1a8a1b1b1b9151a178be3d041632b3d670504600f71ed941634bc1b23dfe79")
MQTT_BROKER = "test.mosquitto.org"
MQTT_PORT = 1883
MQTT_TOPIC_ACCIDENT = "accident/data"

mqtt_client = mqtt.Client(client_id="flask_app", protocol=mqtt.MQTTv311, callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

# Model definitions (unchanged)
class CurrentLocation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

class Accident(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(db.String(50), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    nearest_hospital = db.Column(db.String(200))
    hospital_address = db.Column(db.String(200))
    hospital_phone = db.Column(db.String(50))
    hospital_latitude = db.Column(db.Float)
    hospital_longitude = db.Column(db.Float)
    show = db.Column(db.String(10), default="Yes")

class Emergency(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(db.String(50), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    nearest_hospital = db.Column(db.String(200))
    hospital_address = db.Column(db.String(200))
    hospital_phone = db.Column(db.String(50))
    hospital_latitude = db.Column(db.Float)
    hospital_longitude = db.Column(db.Float)
    show = db.Column(db.String(10), default="Yes")

def update_database_schema():
    """Add hospital_latitude, hospital_longitude, and show columns if tables exist"""
    try:
        conn = sqlite3.connect('accidents.db')
        cursor = conn.cursor()

        # Check if Accident table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='accident'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(accident)")
            columns = [column[1] for column in cursor.fetchall()]
            if "hospital_latitude" not in columns:
                cursor.execute("ALTER TABLE accident ADD COLUMN hospital_latitude FLOAT")
                logger.info("Added hospital_latitude column to Accident table")
            if "hospital_longitude" not in columns:
                cursor.execute("ALTER TABLE accident ADD COLUMN hospital_longitude FLOAT")
                logger.info("Added hospital_longitude column to Accident table")
            if "show" not in columns:
                cursor.execute("ALTER TABLE accident ADD COLUMN show TEXT")
                logger.info("Added show column to Accident table")

        # Check if Emergency table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='emergency'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(emergency)")
            columns = [column[1] for column in cursor.fetchall()]
            if "hospital_latitude" not in columns:
                cursor.execute("ALTER TABLE emergency ADD COLUMN hospital_latitude FLOAT")
                logger.info("Added hospital_latitude column to Emergency table")
            if "hospital_longitude" not in columns:
                cursor.execute("ALTER TABLE emergency ADD COLUMN hospital_longitude FLOAT")
                logger.info("Added hospital_longitude column to Emergency table")
            if "show" not in columns:
                cursor.execute("ALTER TABLE emergency ADD COLUMN show TEXT")
                logger.info("Added show column to Emergency table")

        conn.commit()
        conn.close()
        logger.info("Database schema update completed successfully")
    except Exception as e:
        logger.error(f"Error updating database schema: {e}")

# Initialize database
with app.app_context():
    db.create_all()  # Create tables first
    update_database_schema()  # Then update schema

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

        with app.app_context():
            hospitals = search_nearest_hospitals(latitude, longitude)
            hospital = hospitals[0] if hospitals else None
            hospital_latitude = hospital.get("latitude") if hospital else None
            hospital_longitude = hospital.get("longitude") if hospital else None

            if message_type == "Accident":
                new_accident = Accident(
                    car_id=data['car_id'],
                    latitude=latitude,
                    longitude=longitude,
                    timestamp=timestamp,
                    nearest_hospital=hospital["name"] if hospital else "Not found",
                    hospital_address=hospital["address"] if hospital else "Not found",
                    hospital_phone=hospital["phone"] if hospital else "Not found",
                    hospital_latitude=hospital_latitude,
                    hospital_longitude=hospital_longitude,
                    show=show
                )
                db.session.add(new_accident)
                db.session.commit()
                logger.info("Accident data saved to database")
            elif message_type == "Emergency":
                new_emergency = Emergency(
                    car_id=data['car_id'],
                    latitude=latitude,
                    longitude=longitude,
                    timestamp=timestamp,
                    nearest_hospital=hospital["name"] if hospital else "Not found",
                    hospital_address=hospital["address"] if hospital else "Not found",
                    hospital_phone=hospital["phone"] if hospital else "Not found",
                    hospital_latitude=hospital_latitude,
                    hospital_longitude=hospital_longitude,
                    show=show
                )
                db.session.add(new_emergency)
                db.session.commit()
                logger.info("Emergency data saved to database")

    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON: {e}")
    except Exception as e:
        logger.error(f"Error processing MQTT message: {e}")

# Initialize MQTT client
if not os.environ.get("WERKZEUG_RUN_MAIN"):
    try:
        mqtt_client.on_connect = on_connect
        mqtt_client.on_message = on_message
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        logger.info("MQTT client started successfully")
    except Exception as e:
        logger.error(f"Failed to start MQTT client: {e}")

@app.route('/')
def index():
    all_accidents = Accident.query.filter_by(show="Yes").order_by(Accident.timestamp.desc()).all()
    all_emergencies = Emergency.query.filter_by(show="Yes").order_by(Emergency.timestamp.desc()).all()
    all_records = all_accidents + all_emergencies
    all_records.sort(key=lambda x: x.timestamp, reverse=True)
    return render_template('index.html', all_accidents=all_records)

@app.route('/accidents')
def accidents_only():
    accidents = Accident.query.filter_by(show="Yes").order_by(Accident.timestamp.desc()).all()
    return render_template('index.html', all_accidents=accidents)

@app.route('/emergencies')
def emergencies_only():
    emergencies = Emergency.query.filter_by(show="Yes").order_by(Emergency.timestamp.desc()).all()
    return render_template('index.html', all_accidents=emergencies)

@app.route('/api/nearest_hospitals')
def get_nearest_hospitals():
    latitude = request.args.get('latitude', type=float)
    longitude = request.args.get('longitude', type=float)
    if latitude is None or longitude is None:
        return jsonify({"error": "Latitude and longitude parameters are required"}), 400
    hospitals = search_nearest_hospitals(latitude, longitude)
    return jsonify(hospitals)

# Health check endpoint
@app.route('/health')
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now(tz).isoformat()})

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)