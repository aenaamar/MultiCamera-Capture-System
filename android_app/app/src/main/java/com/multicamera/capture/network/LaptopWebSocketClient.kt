package com.multicamera.capture.network

import android.os.Handler
import android.os.Looper
import android.util.Log
import okhttp3.*
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

interface WebSocketConnectionListener {
    fun onConnected()
    fun onDisconnected(reason: String)
    fun onRegistrationAccepted(cameraId: String)
    fun onCaptureRequest(sessionId: String, captureRequestId: String, timestampMs: Long)
    fun onError(error: String)
}

class LaptopWebSocketClient(
    private val listener: WebSocketConnectionListener
) {
    companion object {
        private const val TAG = "LaptopWebSocketClient"
        private const val HEARTBEAT_INTERVAL_MS = 3000L
        private const val RECONNECT_INTERVAL_MS = 4000L
    }

    private var client: OkHttpClient? = null
    private var webSocket: WebSocket? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private val isRunning = AtomicBoolean(false)
    private var currentHost: String = ""
    private var currentPort: Int = 8000
    private var currentCameraId: String = ""
    private var deviceInspectorProvider: (() -> Pair<String, Pair<Int, JSONObject>>)? = null

    private val heartbeatRunnable = object : Runnable {
        override fun run() {
            if (isRunning.get() && webSocket != null) {
                sendHeartbeat()
                mainHandler.postDelayed(this, HEARTBEAT_INTERVAL_MS)
            }
        }
    }

    private val reconnectRunnable = Runnable {
        if (isRunning.get()) {
            Log.d(TAG, "Attempting automatic reconnection to $currentHost:$currentPort...")
            connectInternal()
        }
    }

    fun start(
        host: String,
        port: Int,
        cameraId: String,
        inspectorProvider: () -> Pair<String, Pair<Int, JSONObject>>
    ) {
        currentHost = host
        currentPort = port
        currentCameraId = cameraId
        deviceInspectorProvider = inspectorProvider
        isRunning.set(true)

        client = OkHttpClient.Builder()
            .readTimeout(10, TimeUnit.SECONDS)
            .writeTimeout(10, TimeUnit.SECONDS)
            .pingInterval(5, TimeUnit.SECONDS)
            .build()

        connectInternal()
    }

    fun stop() {
        isRunning.set(false)
        mainHandler.removeCallbacks(heartbeatRunnable)
        mainHandler.removeCallbacks(reconnectRunnable)
        webSocket?.close(1000, "User disconnected")
        webSocket = null
        client?.dispatcher?.executorService?.shutdown()
        client = null
        mainHandler.post { listener.onDisconnected("Disconnected by user") }
    }

    private fun connectInternal() {
        val url = "ws://$currentHost:$currentPort/ws"
        val request = Request.Builder().url(url).build()

        webSocket = client?.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.i(TAG, "Connected to laptop WebSocket at $url")
                mainHandler.post {
                    listener.onConnected()
                    registerDevice()
                }
                mainHandler.post(heartbeatRunnable)
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val json = JSONObject(text)
                    val type = json.optString("type")
                    Log.d(TAG, "Received WS message: $text")

                    when (type) {
                        "registration_ack" -> {
                            val camId = json.optString("camera_id")
                            mainHandler.post { listener.onRegistrationAccepted(camId) }
                        }
                        "capture_request" -> {
                            val sessionId = json.optString("session_id")
                            val reqId = json.optString("capture_request_id")
                            val ts = json.optLong("timestamp_ms", System.currentTimeMillis())
                            mainHandler.post { listener.onCaptureRequest(sessionId, reqId, ts) }
                        }
                        "status_ack" -> {
                            // Heartbeat confirmed
                        }
                        "error" -> {
                            val msg = json.optString("message", "Unknown server error")
                            mainHandler.post { listener.onError(msg) }
                        }
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "Error parsing incoming message: ${e.message}")
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.w(TAG, "WebSocket closed: $reason ($code)")
                scheduleReconnect("Connection closed: $reason")
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "WebSocket failure: ${t.message}")
                scheduleReconnect("Connection failed: ${t.message}")
            }
        })
    }

    private fun scheduleReconnect(reason: String) {
        mainHandler.removeCallbacks(heartbeatRunnable)
        webSocket = null
        mainHandler.post { listener.onDisconnected(reason) }
        if (isRunning.get()) {
            mainHandler.postDelayed(reconnectRunnable, RECONNECT_INTERVAL_MS)
        }
    }

    private fun registerDevice() {
        try {
            val provider = deviceInspectorProvider?.invoke()
            val deviceModel = provider?.first ?: "Android Phone"
            val batteryLevel = provider?.second?.first ?: -1
            val sensorInfo = provider?.second?.second ?: JSONObject()

            val regObj = JSONObject().apply {
                put("type", "device_register")
                put("camera_id", currentCameraId)
                put("device_name", deviceModel)
                put("battery_level", batteryLevel)
                put("sensor_info", sensorInfo)
            }
            webSocket?.send(regObj.toString())
            Log.i(TAG, "Dispatched registration payload for $currentCameraId")
        } catch (e: Exception) {
            Log.e(TAG, "Failed sending registration: ${e.message}")
        }
    }

    fun sendHeartbeat() {
        try {
            val provider = deviceInspectorProvider?.invoke()
            val batteryLevel = provider?.second?.first ?: -1

            val statusObj = JSONObject().apply {
                put("type", "device_status")
                put("camera_id", currentCameraId)
                put("state", "READY")
                put("battery_level", batteryLevel)
            }
            webSocket?.send(statusObj.toString())
        } catch (e: Exception) {
            Log.e(TAG, "Failed sending heartbeat: ${e.message}")
        }
    }

    fun notifyCaptureStarted(sessionId: String) {
        val obj = JSONObject().apply {
            put("type", "capture_started")
            put("camera_id", currentCameraId)
            put("session_id", sessionId)
        }
        webSocket?.send(obj.toString())
    }

    fun notifyCaptureCompleted(sessionId: String, timestampMs: Long) {
        val obj = JSONObject().apply {
            put("type", "capture_completed")
            put("camera_id", currentCameraId)
            put("session_id", sessionId)
            put("timestamp_ms", timestampMs)
        }
        webSocket?.send(obj.toString())
    }

    fun notifyCaptureFailed(sessionId: String, errorMsg: String) {
        val obj = JSONObject().apply {
            put("type", "capture_failed")
            put("camera_id", currentCameraId)
            put("session_id", sessionId)
            put("error_message", errorMsg)
        }
        webSocket?.send(obj.toString())
    }
}
