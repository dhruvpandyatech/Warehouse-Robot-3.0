import os
import sys
import time
import math
import asyncio
import logging
import base64
import threading
import cv2
import numpy as np
from pymongo import MongoClient
from typing import List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# MongoDB Database Setup
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
print(f"Connecting to MongoDB: {MONGODB_URI}")

try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    db = client["warehouse_db"]
    inventory_col = db["inventory"]
    # Trigger connection test
    client.server_info()
except Exception as e:
    print(f"WARNING: Could not connect to MongoDB server: {e}")

def init_db():
    try:
        if inventory_col.count_documents({}) == 0:
            slots = []
            for slot in range(1, 11):
                row_num = 1 if slot <= 5 else 2
                rack_num = slot if slot <= 5 else slot - 5
                slots.append({
                    "slot_id": slot,
                    "row": row_num,
                    "rack": rack_num,
                    "package_id": None,
                    "last_scanned": None
                })
            inventory_col.insert_many(slots)
            print("Database initialized and seeded with 10 slots.")
        else:
            print("Database already contains records. Skipping seeding.")
    except Exception as e:
        print(f"Error initializing MongoDB database: {e}")

init_db()

# App initialization
app = FastAPI(title="Warehouse Robot Cloud Gateway")

# Enable CORS for Vercel cross-origin frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global states
current_frame_bytes = None
robot_websocket: WebSocket = None
robot_lock = threading.Lock()
connected_websockets: List[WebSocket] = []

# WebSocket Message Broadcast to web clients
def broadcast_ws_message(msg: dict):
    loop = None
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        pass

    async def run_broadcast():
        disconnected = []
        for ws in connected_websockets:
            try:
                await ws.send_json(msg)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            if ws in connected_websockets:
                connected_websockets.remove(ws)

    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(run_broadcast(), loop)


class StartMissionRequest(BaseModel):
    target_qr: str
    mock_mode: bool = True


@app.post("/api/mission/start")
async def start_mission(req: StartMissionRequest):
    global robot_websocket
    if robot_websocket is None:
        return {"status": "error", "message": "No robot agent is connected to the cloud server."}
    
    # Query database for expected location of target_qr
    expected_slot = None
    try:
        res = inventory_col.find_one({"package_id": req.target_qr})
        if res:
            expected_slot = {"row": res["row"], "rack": res["rack"]}
    except Exception as e:
        print(f"Database error during start lookup: {e}")

    try:
        await robot_websocket.send_json({
            "command": "start",
            "target_qr": req.target_qr,
            "mock_mode": req.mock_mode,
            "expected_slot": expected_slot
        })
        return {"status": "success", "message": "Start command forwarded to robot."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to send command to robot: {e}"}


@app.post("/api/mission/audit")
async def start_audit(mock_mode: bool = True):
    global robot_websocket
    if robot_websocket is None:
        return {"status": "error", "message": "No robot agent is connected to the cloud server."}
    
    try:
        await robot_websocket.send_json({
            "command": "audit",
            "mock_mode": mock_mode
        })
        return {"status": "success", "message": "Audit command forwarded to robot."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to send audit command: {e}"}


@app.post("/api/mission/stop")
async def stop_mission():
    global robot_websocket
    if robot_websocket is None:
        return {"status": "error", "message": "No robot agent is connected to the cloud server."}
    
    try:
        await robot_websocket.send_json({"command": "stop"})
        return {"status": "success", "message": "Stop command forwarded to robot."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to send command to robot: {e}"}


@app.get("/api/mission/status")
def get_mission_status():
    global robot_websocket
    if robot_websocket is not None:
        return {"status": "connected"}
    return {"status": "disconnected"}


@app.get("/api/inventory")
def get_inventory():
    try:
        slots = list(inventory_col.find({}, {"_id": 0}).sort("slot_id", 1))
        return slots
    except Exception as e:
        return {"status": "error", "message": f"Failed to fetch inventory: {e}"}


@app.post("/api/inventory/clear")
def clear_inventory():
    try:
        inventory_col.update_many({}, {"$set": {"package_id": None, "last_scanned": None}})
        return {"status": "success", "message": "Inventory cleared"}
    except Exception as e:
        return {"status": "error", "message": f"Failed to clear inventory: {e}"}


# Pre-cache offline frame bytes to avoid compressing on the fly
offline_img = np.zeros((240, 320, 3), dtype=np.uint8)
cv2.putText(offline_img, "Camera Offline", (60, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (90, 90, 90), 2)
_, offline_buffer = cv2.imencode('.jpg', offline_img, [cv2.IMWRITE_JPEG_QUALITY, 50])
OFFLINE_FRAME_BYTES = offline_buffer.tobytes()

# MJPEG frame generator
def gen_frames():
    global current_frame_bytes
    while True:
        frame_to_send = current_frame_bytes if current_frame_bytes is not None else OFFLINE_FRAME_BYTES
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_to_send + b'\r\n')
        time.sleep(0.08)


@app.get("/api/video_feed")
def video_feed():
    return StreamingResponse(gen_frames(), media_type="multipart/x-mixed-replace; boundary=frame")


# WebSocket Endpoint for Robot Agent
@app.websocket("/api/ws/robot")
async def robot_websocket_endpoint(websocket: WebSocket):
    global robot_websocket, current_frame_bytes
    await websocket.accept()
    
    with robot_lock:
        robot_websocket = websocket
        
    broadcast_ws_message({"type": "log", "data": "[SYSTEM] Robot agent connected to cloud gateway."})
    broadcast_ws_message({"type": "robot_status", "data": "connected"})
    
    try:
        while True:
            msg = await websocket.receive_json()
            
            # If msg is a perception frame, decode and set current_frame_bytes directly
            if msg.get("type") == "frame":
                try:
                    current_frame_bytes = base64.b64decode(msg["data"])
                except Exception as e:
                    print(f"Error decoding image: {e}")
            else:
                # Handle slot_scanned updates in database
                if msg.get("type") == "slot_scanned":
                    try:
                        from datetime import datetime, timezone, timedelta
                        ist_tz = timezone(timedelta(hours=5, minutes=30))
                        data = msg["data"]
                        row_val = int(data["row"])
                        rack_val = int(data["rack"])
                        pkg_val = data["package_id"]
                        
                        # De-duplicate: if this package was registered elsewhere, clear it
                        if pkg_val is not None:
                            inventory_col.update_many(
                                {"package_id": pkg_val},
                                {"$set": {"package_id": None, "last_scanned": datetime.now(ist_tz).isoformat()}}
                            )
                        # Update the scanned slot
                        inventory_col.update_one(
                            {"row": row_val, "rack": rack_val},
                            {"$set": {"package_id": pkg_val, "last_scanned": datetime.now(ist_tz).isoformat()}}
                        )
                    except Exception as e:
                        print(f"Error updating slot: {e}")
                
                # Handle target_verified pickup updates in database
                elif msg.get("type") == "target_verified":
                    try:
                        from datetime import datetime, timezone, timedelta
                        ist_tz = timezone(timedelta(hours=5, minutes=30))
                        data = msg["data"]
                        row_val = int(data["row"])
                        rack_val = int(data["rack"])
                        
                        # Clear target package since it has been picked up
                        inventory_col.update_one(
                            {"row": row_val, "rack": rack_val},
                            {"$set": {"package_id": None, "last_scanned": datetime.now(ist_tz).isoformat()}}
                        )
                    except Exception as e:
                        print(f"Error handling target_verified: {e}")

                # Forward other telemetries, states and log messages to browser clients
                broadcast_ws_message(msg)
                
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Robot WebSocket error: {e}")
    finally:
        with robot_lock:
            if robot_websocket == websocket:
                robot_websocket = None
        current_frame_bytes = None
        broadcast_ws_message({"type": "log", "data": "[SYSTEM] Robot agent disconnected."})
        broadcast_ws_message({"type": "robot_status", "data": "disconnected"})
        broadcast_ws_message({"type": "status", "data": "idle"})


# WebSocket Endpoint for Browser Web Clients
@app.websocket("/api/ws")
async def browser_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_websockets.append(websocket)
    
    # Send current connection state of the robot to browser
    global robot_websocket
    if robot_websocket is not None:
        await websocket.send_json({"type": "robot_status", "data": "connected"})
    else:
        await websocket.send_json({"type": "robot_status", "data": "disconnected"})

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in connected_websockets:
            connected_websockets.remove(websocket)
    except Exception:
        if websocket in connected_websockets:
            connected_websockets.remove(websocket)


# Mount static files
os.makedirs(os.path.join(os.path.dirname(__file__), "static"), exist_ok=True)
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
