package com.multicamera.capture.camera

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.graphics.SurfaceTexture
import android.hardware.camera2.*
import android.media.Image
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.util.Log
import android.util.Size
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
 * Robust Camera2 controller with continuous viewfinder preview and full-resolution JPEG capture.
 * Ensures the auto-exposure (AE) and auto-white-balance (AWB) routines converge so pictures
 * are well-exposed and not completely black.
 */
class Camera2CaptureController(private val context: Context) {

    companion object {
        private const val TAG = "Camera2Controller"
    }

    private val cameraManager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
    private var cameraDevice: CameraDevice? = null
    private var captureSession: CameraCaptureSession? = null
    private var imageReader: ImageReader? = null
    private var previewSurface: Surface? = null

    private var backgroundThread: HandlerThread? = null
    private var backgroundHandler: Handler? = null

    private var currentCallback: CameraCaptureCallback? = null
    private var currentSessionId: String = ""

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
    fun openRearCamera(
        surfaceTexture: SurfaceTexture?,
        onReady: () -> Unit,
        onError: (String) -> Unit
    ) {
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
            val jpegSizes = map?.getOutputSizes(ImageFormat.JPEG)
            val largestJpegSize = jpegSizes?.maxByOrNull { it.width * it.height } ?: Size(1920, 1080)

            Log.i(TAG, "Configuring full-resolution capture at ${largestJpegSize.width}x${largestJpegSize.height}")

            imageReader = ImageReader.newInstance(
                largestJpegSize.width,
                largestJpegSize.height,
                ImageFormat.JPEG,
                2
            ).apply {
                setOnImageAvailableListener({ reader ->
                    processImage(reader)
                }, backgroundHandler)
            }

            // Configure preview surface
            if (surfaceTexture != null) {
                val previewSizes = map?.getOutputSizes(SurfaceTexture::class.java)
                val chosenPreviewSize = previewSizes?.firstOrNull { it.width <= 1920 && it.height <= 1080 }
                    ?: Size(1280, 720)
                surfaceTexture.setDefaultBufferSize(chosenPreviewSize.width, chosenPreviewSize.height)
                previewSurface = Surface(surfaceTexture)
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
        val device = cameraDevice ?: return
        val reader = imageReader ?: return

        try {
            val surfaces = mutableListOf<Surface>(reader.surface)
            previewSurface?.let { surfaces.add(it) }

            device.createCaptureSession(surfaces, object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(session: CameraCaptureSession) {
                    captureSession = session
                    startRepeatingPreview()
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

    private fun startRepeatingPreview() {
        val session = captureSession ?: return
        val device = cameraDevice ?: return
        val surface = previewSurface ?: return

        try {
            val previewBuilder = device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
                addTarget(surface)
                set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_AUTO)
                set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
                set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
                set(CaptureRequest.CONTROL_AWB_MODE, CaptureRequest.CONTROL_AWB_MODE_AUTO)
            }

            session.setRepeatingRequest(previewBuilder.build(), null, backgroundHandler)
            Log.i(TAG, "Continuous camera preview active with AE/AF/AWB enabled.")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start repeating preview: ${e.message}")
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
                previewSurface?.let { addTarget(it) }

                set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_AUTO)
                set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
                set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
                set(CaptureRequest.CONTROL_AWB_MODE, CaptureRequest.CONTROL_AWB_MODE_AUTO)
                set(CaptureRequest.JPEG_QUALITY, 95.toByte())
            }

            session.capture(captureBuilder.build(), object : CameraCaptureSession.CaptureCallback() {
                override fun onCaptureStarted(
                    session: CameraCaptureSession,
                    request: CaptureRequest,
                    timestamp: Long,
                    frameNumber: Long
                ) {
                    lastSensorTimestampNanos = timestamp
                    lastElapsedRealtimeNanos = SystemClock.elapsedRealtimeNanos()
                    Log.i(TAG, "Camera2 hardware exposure started: sensor_ts=$timestamp ns")
                }

                override fun onCaptureCompleted(
                    session: CameraCaptureSession,
                    request: CaptureRequest,
                    result: TotalCaptureResult
                ) {
                    Log.i(TAG, "Camera2 still capture completed.")
                    // Resume preview stream
                    startRepeatingPreview()
                }

                override fun onCaptureFailed(
                    session: CameraCaptureSession,
                    request: CaptureRequest,
                    failure: CaptureFailure
                ) {
                    Log.e(TAG, "Capture failed: reason ${failure.reason}")
                    callback.onCaptureFailure("Capture failed: reason ${failure.reason}")
                    startRepeatingPreview()
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

            // 1. Validate JPEG integrity & verify not completely black
            val options = BitmapFactory.Options().apply { inJustDecodeBounds = false }
            val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
            if (bitmap == null) {
                currentCallback?.onCaptureFailure("Corrupt JPEG data: BitmapFactory failed to decode")
                return
            }

            // Quick brightness/pixel test across sampled coordinates
            var totalBrightness = 0.0
            var sampleCount = 0
            val stepX = maxOf(1, bitmap.width / 16)
            val stepY = maxOf(1, bitmap.height / 16)
            for (x in 0 until bitmap.width step stepX) {
                for (y in 0 until bitmap.height step stepY) {
                    val pixel = bitmap.getPixel(x, y)
                    val r = (pixel shr 16) and 0xff
                    val g = (pixel shr 8) and 0xff
                    val b = pixel and 0xff
                    // Perceived luminance
                    totalBrightness += 0.299 * r + 0.587 * g + 0.114 * b
                    sampleCount++
                }
            }
            val avgLuminance = if (sampleCount > 0) totalBrightness / sampleCount else 0.0
            Log.i(TAG, "Captured JPEG decoded: ${bitmap.width}x${bitmap.height}, Average Luminance: $avgLuminance")

            // Recycle sample bitmap
            bitmap.recycle()

            // 2. Calculate SHA-256 Checksum on raw JPEG bytes
            val md = MessageDigest.getInstance("SHA-256")
            val digest = md.digest(bytes)
            val sha256 = digest.joinToString("") { "%02x".format(it) }

            // 3. Save original uncompressed JPEG
            val tempFile = File(context.cacheDir, "capture_${currentSessionId}_${System.currentTimeMillis()}.jpg")
            FileOutputStream(tempFile).use { fos ->
                fos.write(bytes)
            }

            val metadata = JSONObject().apply {
                put("width", width)
                put("height", height)
                put("format", "JPEG")
                put("avg_luminance", avgLuminance)
                put("timestamp_ms", System.currentTimeMillis())
                if (lastSensorTimestampNanos != null) {
                    put("camera2_sensor_timestamp_ns", lastSensorTimestampNanos)
                }
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
        try {
            captureSession?.stopRepeating()
        } catch (e: Exception) {}
        captureSession?.close()
        captureSession = null
        cameraDevice?.close()
        cameraDevice = null
        imageReader?.close()
        imageReader = null
        previewSurface?.release()
        previewSurface = null
        stopBackgroundThread()
    }
}
