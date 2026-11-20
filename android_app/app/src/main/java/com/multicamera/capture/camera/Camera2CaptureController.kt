package com.multicamera.capture.camera

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.ImageFormat
import android.hardware.camera2.*
import android.media.Image
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.util.Log
import android.view.Surface
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest

interface CameraCaptureCallback {
    fun onCaptureSuccess(capturedFile: File, sha256: String, width: Int, height: Int, metadata: JSONObject)
    fun onCaptureFailure(error: String)
}

/**
 * Robust Camera2 controller responsible for background session setup and uncompressed JPEG acquisition.
 * Records multi-source timestamps (Unix epoch ms, Camera2 sensor timestamp nanos, and elapsedRealtime nanos).
 */
class Camera2CaptureController(private val context: Context) {

    companion object {
        private const val TAG = "Camera2Controller"
    }

    private val cameraManager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
    private var cameraDevice: CameraDevice? = null
    private var captureSession: CameraCaptureSession? = null
    private var imageReader: ImageReader? = null

    private var backgroundThread: HandlerThread? = null
    private var backgroundHandler: Handler? = null

    private var currentCallback: CameraCaptureCallback? = null
    private var currentSessionId: String = ""

    // Hardware capture result capture
    @Volatile
    private var lastSensorTimestampNanos: Long? = null
    @Volatile
    private var lastElapsedRealtimeNanos: Long? = null

    fun startBackgroundThread() {
        if (backgroundThread == null) {
            backgroundThread = HandlerThread("CameraBackground").also { it.start() }
            backgroundHandler = Handler(backgroundThread!!.looper)
        }
    }

    fun stopBackgroundThread() {
        backgroundThread?.quitSafely()
        try {
            backgroundThread?.join()
            backgroundThread = null
            backgroundHandler = null
        } catch (e: InterruptedException) {
            Log.e(TAG, "Error stopping thread: ${e.message}")
        }
    }

    @SuppressLint("MissingPermission")
    fun openRearCamera(onReady: () -> Unit, onError: (String) -> Unit) {
        startBackgroundThread()
        try {
            var rearId: String? = null
            for (id in cameraManager.cameraIdList) {
                val chars = cameraManager.getCameraCharacteristics(id)
                if (chars.get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_BACK) {
                    rearId = id
                    break
                }
            }

            if (rearId == null) {
                onError("No rear-facing camera detected on this device")
                return
            }

            val characteristics = cameraManager.getCameraCharacteristics(rearId)
            val map = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
            val sizes = map?.getOutputSizes(ImageFormat.JPEG)
            val largestSize = sizes?.maxByOrNull { it.width * it.height } ?: android.util.Size(1920, 1080)

            imageReader = ImageReader.newInstance(
                largestSize.width,
                largestSize.height,
                ImageFormat.JPEG,
                2
            ).apply {
                setOnImageAvailableListener({ reader ->
                    processImage(reader)
                }, backgroundHandler)
            }

            cameraManager.openCamera(rearId, object : CameraDevice.StateCallback() {
                override fun onOpened(camera: CameraDevice) {
                    cameraDevice = camera
                    createCaptureSession(onReady, onError)
                }

                override fun onDisconnected(camera: CameraDevice) {
                    camera.close()
                    cameraDevice = null
                }

                override fun onError(camera: CameraDevice, error: Int) {
                    camera.close()
                    cameraDevice = null
                    onError("CameraDevice error code: $error")
                }
            }, backgroundHandler)

        } catch (e: Exception) {
            onError("Failed opening camera: ${e.message}")
        }
    }

    private fun createCaptureSession(onReady: () -> Unit, onError: (String) -> Unit) {
        val reader = imageReader ?: return
        try {
            val surfaces = listOf(reader.surface)
            cameraDevice?.createCaptureSession(surfaces, object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(session: CameraCaptureSession) {
                    captureSession = session
                    onReady()
                }

                override fun onConfigureFailed(session: CameraCaptureSession) {
                    onError("Failed to configure Camera2 capture session")
                }
            }, backgroundHandler)
        } catch (e: Exception) {
            onError("Error creating session: ${e.message}")
        }
    }

    fun takePicture(sessionId: String, callback: CameraCaptureCallback) {
        currentSessionId = sessionId
        currentCallback = callback
        lastSensorTimestampNanos = null
        lastElapsedRealtimeNanos = null

        val session = captureSession
        val reader = imageReader
        val device = cameraDevice

        if (session == null || reader == null || device == null) {
            callback.onCaptureFailure("Camera is not initialized or ready")
            return
        }

        try {
            val captureBuilder = device.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE).apply {
                addTarget(reader.surface)
                // Use auto-exposure and auto-focus for crisp multi-angle images
                set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_AUTO)
                set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
                set(CaptureRequest.JPEG_QUALITY, 95.toByte())
            }

            session.capture(captureBuilder.build(), object : CameraCaptureSession.CaptureCallback() {
                override fun onCaptureStarted(
                    session: CameraCaptureSession,
                    request: CaptureRequest,
                    timestamp: Long,
                    frameNumber: Long
                ) {
                    // Camera2 SENSOR_TIMESTAMP (exposure start in nanoseconds)
                    lastSensorTimestampNanos = timestamp
                    lastElapsedRealtimeNanos = SystemClock.elapsedRealtimeNanos()
                }

                override fun onCaptureFailed(
                    session: CameraCaptureSession,
                    request: CaptureRequest,
                    failure: CaptureFailure
                ) {
                    callback.onCaptureFailure("Capture failed: reason ${failure.reason}")
                }
            }, backgroundHandler)

        } catch (e: Exception) {
            callback.onCaptureFailure("Exception during takePicture: ${e.message}")
        }
    }

    private fun processImage(reader: ImageReader) {
        var image: Image? = null
        try {
            image = reader.acquireLatestImage()
            if (image == null) return

            val planes = image.planes
            val buffer = planes[0].buffer
            val bytes = ByteArray(buffer.remaining())
            buffer.get(bytes)

            val width = image.width
            val height = image.height

            // Calculate SHA-256
            val md = MessageDigest.getInstance("SHA-256")
            val digest = md.digest(bytes)
            val sha256 = digest.joinToString("") { "%02x".format(it) }

            // Write to temporary cache file
            val tempFile = File(context.cacheDir, "capture_${currentSessionId}_${System.currentTimeMillis()}.jpg")
            FileOutputStream(tempFile).use { fos ->
                fos.write(bytes)
            }

            val metadata = JSONObject().apply {
                put("width", width)
                put("height", height)
                put("format", "JPEG")
                // System wall-clock time
                put("timestamp_ms", System.currentTimeMillis())
                // Camera2 hardware exposure start time (nanoseconds)
                if (lastSensorTimestampNanos != null) {
                    put("camera2_sensor_timestamp_ns", lastSensorTimestampNanos)
                }
                // Monotonic device elapsed realtime (nanoseconds)
                if (lastElapsedRealtimeNanos != null) {
                    put("elapsed_realtime_ns", lastElapsedRealtimeNanos)
                }
                put("timestamp_source", "System.currentTimeMillis() (wall-clock) + Camera2 SENSOR_TIMESTAMP (monotonic)")
            }

            currentCallback?.onCaptureSuccess(tempFile, sha256, width, height, metadata)

        } catch (e: Exception) {
            Log.e(TAG, "Failed processing image: ${e.message}")
            currentCallback?.onCaptureFailure("Failed saving image: ${e.message}")
        } finally {
            image?.close()
        }
    }

    fun close() {
        captureSession?.close()
        captureSession = null
        cameraDevice?.close()
        cameraDevice = null
        imageReader?.close()
        imageReader = null
        stopBackgroundThread()
    }
}
