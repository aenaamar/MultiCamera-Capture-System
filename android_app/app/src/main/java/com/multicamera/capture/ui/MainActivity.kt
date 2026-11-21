package com.multicamera.capture.ui

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.SurfaceTexture
import android.os.Bundle
import android.util.Log
import android.view.TextureView
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
        private const val TAG = "MainActivity"
        private const val PERMISSIONS_REQUEST_CODE = 1001
    }

    private lateinit var binding: ActivityMainBinding
    private lateinit var deviceInspector: DeviceInspector
    private lateinit var wsClient: LaptopWebSocketClient
    private lateinit var cameraController: Camera2CaptureController
    private lateinit var imageUploader: ImageUploader

    private var isConnected = false
    private var isCameraReady = false
    private var surfaceTextureAvailable = false

    // Deduplication tracking: prevent duplicate captures for same captureRequestId
    private val handledCaptureRequests = Collections.synchronizedSet(HashSet<String>())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.i(TAG, "[LIFECYCLE] onCreate called")
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        deviceInspector = DeviceInspector(this)
        wsClient = LaptopWebSocketClient(this)
        cameraController = Camera2CaptureController(this)
        imageUploader = ImageUploader()

        setupUI()
        setupTextureView()
    }

    private fun setupUI() {
        // Auto-populate camera_id from last saved slot (defaults to "camera_01" on first run).
        // The user can change this in the text field; the new value is persisted on Connect.
        val savedSlot = deviceInspector.getSavedCameraSlot()
        binding.etCameraId.setText(savedSlot)
        binding.etServerHost.setText("192.168.43.204")

        binding.btnConnect.setOnClickListener {
            if (!isConnected) {
                connectToLaptop()
            } else {
                disconnectFromLaptop()
            }
        }
        updateBatteryDisplay()
    }

    private fun setupTextureView() {
        binding.viewFinder.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
            override fun onSurfaceTextureAvailable(surface: SurfaceTexture, width: Int, height: Int) {
                Log.i(TAG, "[TEXTURE] onSurfaceTextureAvailable: ${width}x${height}")
                surfaceTextureAvailable = true
                checkPermissionsAndInitCamera()
            }

            override fun onSurfaceTextureSizeChanged(surface: SurfaceTexture, width: Int, height: Int) {
                Log.d(TAG, "[TEXTURE] onSurfaceTextureSizeChanged: ${width}x${height}")
            }

            override fun onSurfaceTextureDestroyed(surface: SurfaceTexture): Boolean {
                Log.i(TAG, "[TEXTURE] onSurfaceTextureDestroyed")
                surfaceTextureAvailable = false
                cameraController.close()
                return true
            }

            override fun onSurfaceTextureUpdated(surface: SurfaceTexture) {}
        }
    }

    private fun updateBatteryDisplay() {
        val batt = deviceInspector.batteryLevel
        binding.tvBattery.text = if (batt >= 0) "Battery: $batt%" else "Battery: N/A"
    }

    private fun checkPermissionsAndInitCamera() {
        val cameraPerm = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
        val hasPerm = (cameraPerm == PackageManager.PERMISSION_GRANTED)
        Log.i(TAG, "[PERMISSION] checkSelfPermission(CAMERA) = $cameraPerm (granted=$hasPerm)")

        if (!hasPerm) {
            logMessage("Requesting CAMERA runtime permission...")
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
        Log.i(TAG, "[PERMISSION] onRequestPermissionsResult: reqCode=$requestCode")
        if (requestCode == PERMISSIONS_REQUEST_CODE && grantResults.isNotEmpty() &&
            grantResults[0] == PackageManager.PERMISSION_GRANTED
        ) {
            logMessage("CAMERA permission granted by user.")
            initCamera()
        } else {
            logMessage("Camera permission is required for multi-angle capture.")
            Toast.makeText(this, "Camera permission denied", Toast.LENGTH_LONG).show()
        }
    }

    private fun initCamera() {
        if (!surfaceTextureAvailable) {
            logMessage("Waiting for Viewfinder SurfaceTexture...")
            return
        }

        val cameraPerm = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
        if (cameraPerm != PackageManager.PERMISSION_GRANTED) {
            logMessage("Cannot init camera: CAMERA permission not granted.")
            return
        }

        logMessage("Opening Camera2 rear sensor and live viewfinder...")
        val texture = binding.viewFinder.surfaceTexture

        cameraController.openRearCamera(
            surfaceTexture = texture,
            onReady = {
                runOnUiThread {
                    isCameraReady = true
                    binding.tvCameraReady.text = "Camera: READY"
                    binding.tvCameraReady.setTextColor(0xFF4CAF50.toInt())
                    binding.tvPreviewOverlay.text = "LIVE PREVIEW - READY"
                    binding.tvPreviewOverlay.setTextColor(0xFF00E676.toInt())
                    logMessage("Camera2 initialized. Live preview active.")
                }
            },
            onError = { error ->
                runOnUiThread {
                    isCameraReady = false
                    binding.tvCameraReady.text = "Camera: ERROR"
                    binding.tvCameraReady.setTextColor(0xFFFF5252.toInt())
                    binding.tvPreviewOverlay.text = "CAMERA ERROR"
                    binding.tvPreviewOverlay.setTextColor(0xFFFF5252.toInt())
                    logMessage("Camera error: $error")
                    if (error.contains("ERROR_CAMERA_DISABLED")) {
                        logMessage("Diag: Check Quick Settings 'Camera access' toggle & App Permissions.")
                    }
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

        // Persist the user's chosen camera slot so it auto-populates next time
        deviceInspector.saveCameraSlot(camId)

        val port = portStr.toIntOrNull() ?: 8000
        logMessage("Connecting to ws://$host:$port/ws as $camId (device: ${deviceInspector.deviceId.takeLast(8)})...")

        wsClient.start(host, port, camId) {
            // Build the full device info bundle for the registration payload
            val deviceInfo = deviceInspector.getDeviceInfoJson()
            val sensorInfo = deviceInspector.getCameraCharacteristicsJson()
            deviceInfo.put("sensor_info", sensorInfo)
            deviceInfo
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

    override fun onRegistrationAccepted(cameraId: String, deviceId: String?) {
        val shortId = deviceId?.takeLast(8) ?: "--"
        binding.tvStatus.text = "Status: Registered ($cameraId) - READY"
        binding.tvStatus.setTextColor(0xFF4CAF50.toInt())
        logMessage("Registration confirmed for $cameraId (device: …$shortId) by laptop.")
    }

    override fun onCaptureRequest(sessionId: String, captureRequestId: String, timestampMs: Long) {
        if (handledCaptureRequests.contains(captureRequestId)) {
            logMessage("Duplicate capture request ignored: $captureRequestId")
            return
        }
        handledCaptureRequests.add(captureRequestId)

        if (!isCameraReady) {
            logMessage("Cannot capture: Camera2 not ready yet.")
            wsClient.notifyCaptureFailed(sessionId, "Camera sensor not ready")
            return
        }

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
                val luminance = metadata.optDouble("avg_luminance", 0.0)
                logMessage("Captured photo (${width}x${height}, Lum: ${"%.1f".format(luminance)}). Uploading...")
                wsClient.notifyCaptureCompleted(sessionId, captureTs)

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
                            logMessage("Upload completed & verified by laptop (SHA-256 matched)!")
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

    override fun onResume() {
        super.onResume()
        Log.i(TAG, "[LIFECYCLE] onResume called")
        if (binding.viewFinder.isAvailable && !isCameraReady) {
            surfaceTextureAvailable = true
            checkPermissionsAndInitCamera()
        }
    }

    override fun onPause() {
        super.onPause()
        Log.i(TAG, "[LIFECYCLE] onPause called")
        cameraController.close()
        isCameraReady = false
    }

    override fun onDestroy() {
        super.onDestroy()
        Log.i(TAG, "[LIFECYCLE] onDestroy called")
        wsClient.stop()
        cameraController.close()
    }
}
