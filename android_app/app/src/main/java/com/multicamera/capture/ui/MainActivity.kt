package com.multicamera.capture.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.WindowManager
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.multicamera.capture.camera.Camera2CaptureController
import com.multicamera.capture.camera.CameraCaptureCallback
import com.multicamera.capture.databinding.ActivityMainBinding
import com.multicamera.capture.device.DeviceInspector
import com.multicamera.capture.network.ImageUploadCallback
import com.multicamera.capture.network.ImageUploader
import com.multicamera.capture.network.LaptopWebSocketClient
import com.multicamera.capture.network.WebSocketConnectionListener
import org.json.JSONObject
import java.io.File
import java.util.Collections

class MainActivity : AppCompatActivity(), WebSocketConnectionListener {

    companion object {
        private const val PERMISSIONS_REQUEST_CODE = 1001
    }

    private lateinit var binding: ActivityMainBinding
    private lateinit var deviceInspector: DeviceInspector
    private lateinit var wsClient: LaptopWebSocketClient
    private lateinit var cameraController: Camera2CaptureController
    private lateinit var imageUploader: ImageUploader

    private var isConnected = false
    private var isCameraReady = false

    // Deduplication tracking: prevent duplicate captures for same captureRequestId
    private val handledCaptureRequests = Collections.synchronizedSet(HashSet<String>())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        deviceInspector = DeviceInspector(this)
        wsClient = LaptopWebSocketClient(this)
        cameraController = Camera2CaptureController(this)
        imageUploader = ImageUploader()

        setupUI()
        checkPermissionsAndInitCamera()
    }

    private fun setupUI() {
        val defaultId = "camera_01"
        binding.etCameraId.setText(defaultId)
        binding.etServerHost.setText("192.168.1.6")

        binding.btnConnect.setOnClickListener {
            if (!isConnected) {
                connectToLaptop()
            } else {
                disconnectFromLaptop()
            }
        }
        updateBatteryDisplay()
    }

    private fun updateBatteryDisplay() {
        val batt = deviceInspector.batteryLevel
        binding.tvBattery.text = if (batt >= 0) "Battery: $batt%" else "Battery: N/A"
    }

    private fun checkPermissionsAndInitCamera() {
        val cameraPerm = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
        if (cameraPerm != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.CAMERA),
                PERMISSIONS_REQUEST_CODE
            )
        } else {
            initCamera()
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PERMISSIONS_REQUEST_CODE && grantResults.isNotEmpty() &&
            grantResults[0] == PackageManager.PERMISSION_GRANTED
        ) {
            initCamera()
        } else {
            logMessage("Camera permission is required for multi-angle capture.")
            Toast.makeText(this, "Camera permission denied", Toast.LENGTH_LONG).show()
        }
    }

    private fun initCamera() {
        logMessage("Initializing Camera2 rear sensor...")
        cameraController.openRearCamera(
            onReady = {
                runOnUiThread {
                    isCameraReady = true
                    binding.tvCameraReady.text = "Camera Sensor: READY (Rear Lens)"
                    binding.tvCameraReady.setTextColor(0xFF4CAF50.toInt())
                    logMessage("Camera2 rear sensor initialized and ready.")
                }
            },
            onError = { error ->
                runOnUiThread {
                    isCameraReady = false
                    binding.tvCameraReady.text = "Camera Sensor: ERROR ($error)"
                    binding.tvCameraReady.setTextColor(0xFFFF5252.toInt())
                    logMessage("Camera error: $error")
                }
            }
        )
    }

    private fun connectToLaptop() {
        val host = binding.etServerHost.text.toString().trim()
        val portStr = binding.etServerPort.text.toString().trim()
        val camId = binding.etCameraId.text.toString().trim()

        if (host.isEmpty() || portStr.isEmpty() || camId.isEmpty()) {
            Toast.makeText(this, "Please enter server IP, port, and camera ID", Toast.LENGTH_SHORT).show()
            return
        }

        val port = portStr.toIntOrNull() ?: 8000
        logMessage("Connecting to ws://$host:$port/ws as $camId...")

        wsClient.start(host, port, camId) {
            val model = deviceInspector.deviceModel
            val batt = deviceInspector.batteryLevel
            val chars = deviceInspector.getCameraCharacteristicsJson()
            Pair(model, Pair(batt, chars))
        }
    }

    private fun disconnectFromLaptop() {
        wsClient.stop()
    }

    override fun onConnected() {
        isConnected = true
        binding.btnConnect.text = getString(com.multicamera.capture.R.string.disconnect)
        binding.btnConnect.setBackgroundColor(0xFFF44336.toInt())
        binding.tvStatus.text = getString(com.multicamera.capture.R.string.status_connected)
        binding.tvStatus.setTextColor(0xFF2196F3.toInt())
        updateBatteryDisplay()
        logMessage("Connected to laptop server.")
    }

    override fun onDisconnected(reason: String) {
        isConnected = false
        binding.btnConnect.text = getString(com.multicamera.capture.R.string.connect)
        binding.btnConnect.setBackgroundColor(0xFF2196F3.toInt())
        binding.tvStatus.text = getString(com.multicamera.capture.R.string.status_disconnected)
        binding.tvStatus.setTextColor(0xFFFF5252.toInt())
        logMessage("Disconnected: $reason")
    }

    override fun onRegistrationAccepted(cameraId: String) {
        binding.tvStatus.text = "Status: Registered ($cameraId) - READY"
        binding.tvStatus.setTextColor(0xFF4CAF50.toInt())
        logMessage("Registration confirmed for $cameraId by laptop.")
    }

    override fun onCaptureRequest(sessionId: String, captureRequestId: String, timestampMs: Long) {
        // Prevent duplicate captures if the same request ID arrives more than once
        if (handledCaptureRequests.contains(captureRequestId)) {
            logMessage("Duplicate capture request ignored: $captureRequestId")
            return
        }
        handledCaptureRequests.add(captureRequestId)

        logMessage("--> Capture request executing: $sessionId ($captureRequestId)")
        wsClient.notifyCaptureStarted(sessionId)

        val host = binding.etServerHost.text.toString().trim()
        val port = binding.etServerPort.text.toString().toIntOrNull() ?: 8000
        val camId = binding.etCameraId.text.toString().trim()

        cameraController.takePicture(sessionId, object : CameraCaptureCallback {
            override fun onCaptureSuccess(
                capturedFile: File,
                sha256: String,
                width: Int,
                height: Int,
                metadata: JSONObject
            ) {
                val captureTs = System.currentTimeMillis()
                metadata.put("capture_request_id", captureRequestId)
                logMessage("Captured ($width x $height). Starting reliable upload (SHA256: ${sha256.take(8)}...)...")
                wsClient.notifyCaptureCompleted(sessionId, captureTs)

                // Automatic Image Upload with exponential backoff retry
                imageUploader.uploadImage(
                    host = host,
                    port = port,
                    sessionId = sessionId,
                    cameraId = camId,
                    captureRequestId = captureRequestId,
                    imageFile = capturedFile,
                    sha256Checksum = sha256,
                    metadata = metadata,
                    callback = object : ImageUploadCallback {
                        override fun onUploadSuccess(response: JSONObject) {
                            logMessage("Upload completed & verified by laptop!")
                            capturedFile.delete()
                        }

                        override fun onUploadFailure(error: String) {
                            logMessage("Upload failed after retries: $error")
                        }
                    }
                )
            }

            override fun onCaptureFailure(error: String) {
                logMessage("Capture error: $error")
                wsClient.notifyCaptureFailed(sessionId, error)
            }
        })
    }

    override fun onError(error: String) {
        logMessage("Server error: $error")
    }

    private fun logMessage(msg: String) {
        runOnUiThread {
            binding.tvLog.append("\n$msg")
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        wsClient.stop()
        cameraController.close()
    }
}
