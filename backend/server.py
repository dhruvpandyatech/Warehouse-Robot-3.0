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
from typing import List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
    
    try:
        await robot_websocket.send_json({
            "command": "start",
            "target_qr": req.target_qr,
            "mock_mode": req.mock_mode
        })
        return {"status": "success", "message": "Start command forwarded to robot."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to send command to robot: {e}"}


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
