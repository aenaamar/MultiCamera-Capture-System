package com.multicamera.capture.camera

import android.annotation.SuppressLint
import android.content.Context
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
import java.util.concurrent.Semaphore
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

interface CameraCaptureCallback {
    fun onCaptureSuccess(capturedFile: File, sha256: String, width: Int, height: Int, metadata: JSONObject)
    fun onCaptureFailure(error: String)
}

/**
 * Robust Camera2 controller with generation-tracked callbacks, guaranteed deferred-cleanup,
 * frame validation, and clean lifecycle resource isolation.
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

    // Concurrency guards
    private val cameraOpenCloseLock = Semaphore(1)
    private val isOpening = AtomicBoolean(false)
    val isReady = AtomicBoolean(false)

    // Generation token to invalidate stale callbacks across open/close cycles
    private val activeSessionGeneration = AtomicInteger(0)

    private var currentCallback: CameraCaptureCallback? = null
    private var currentSessionId: String = ""

    @Volatile
    private var lastSensorTimestampNanos: Long? = null
    @Volatile
    private var lastElapsedRealtimeNanos: Long? = null

    @Synchronized
    fun startBackgroundThread() {
        if (backgroundThread == null) {
            backgroundThread = HandlerThread("CameraBackground").also { it.start() }
            backgroundHandler = Handler(backgroundThread!!.looper)
            Log.d(TAG, "[DIAGNOSTIC] Background handler thread started.")
        }
    }

    @Synchronized
    fun stopBackgroundThread() {
        backgroundThread?.quitSafely()
        try {
            backgroundThread?.join(1000)
            backgroundThread = null
            backgroundHandler = null
            Log.d(TAG, "[DIAGNOSTIC] Background handler thread stopped.")
        } catch (e: InterruptedException) {
            Log.e(TAG, "[DIAGNOSTIC] Error stopping thread: ${e.message}")
        }
    }

    @SuppressLint("MissingPermission")
    fun openRearCamera(
        surfaceTexture: SurfaceTexture?,
        onReady: () -> Unit,
        onError: (String) -> Unit
    ) {
        if (isOpening.get() || isReady.get()) {
            Log.w(TAG, "[DIAGNOSTIC] Camera is already open or opening. Skipping redundant call.")
            return
        }

        val thisGeneration = activeSessionGeneration.incrementAndGet()
        isOpening.set(true)
        isReady.set(false)

        startBackgroundThread()

        backgroundHandler?.post {
            if (!cameraOpenCloseLock.tryAcquire(2500, TimeUnit.MILLISECONDS)) {
                Log.e(TAG, "[DIAGNOSTIC] Timeout acquiring cameraOpenCloseLock.")
                if (activeSessionGeneration.get() == thisGeneration) {
                    isOpening.set(false)
                    onError("Timeout acquiring camera lock. Camera resource busy.")
                }
                return@post
            }

            try {
                if (activeSessionGeneration.get() != thisGeneration) {
                    Log.w(TAG, "[DIAGNOSTIC] Session generation mismatch before open. Aborting.")
                    cameraOpenCloseLock.release()
                    return@post
                }

                val cameraIds = cameraManager.cameraIdList
                Log.i(TAG, "[DIAGNOSTIC] Available Camera IDs on device: ${cameraIds.joinToString()}")

                var rearId: String? = null
                for (id in cameraIds) {
                    try {
                        val chars = cameraManager.getCameraCharacteristics(id)
                        val facing = chars.get(CameraCharacteristics.LENS_FACING)
                        val hwLevel = chars.get(CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL)
                        Log.i(TAG, "[DIAGNOSTIC] Camera ID '$id': facing=$facing, hwLevel=$hwLevel")
                        if (facing == CameraCharacteristics.LENS_FACING_BACK && rearId == null) {
                            rearId = id
                        }
                    } catch (e: Exception) {
                        Log.w(TAG, "[DIAGNOSTIC] Failed reading characteristics for Camera ID '$id': ${e.message}")
                    }
                }

                if (rearId == null) {
                    Log.e(TAG, "[DIAGNOSTIC] No rear-facing camera detected on this device!")
                    if (activeSessionGeneration.get() == thisGeneration) {
                        isOpening.set(false)
                        onError("No rear-facing camera detected on this device.")
                    }
                    cameraOpenCloseLock.release()
                    return@post
                }

                Log.i(TAG, "[DIAGNOSTIC] Target rear camera selected: ID '$rearId'")
                val characteristics = cameraManager.getCameraCharacteristics(rearId)
                val map = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)

                val jpegSizes = map?.getOutputSizes(ImageFormat.JPEG)
                val largestJpegSize = jpegSizes?.maxByOrNull { it.width * it.height } ?: Size(1920, 1080)
                Log.i(TAG, "[DIAGNOSTIC] Configured full-res JPEG size: ${largestJpegSize.width}x${largestJpegSize.height}")

                imageReader?.close()
                imageReader = ImageReader.newInstance(
                    largestJpegSize.width,
                    largestJpegSize.height,
                    ImageFormat.JPEG,
                    2
                ).apply {
                    setOnImageAvailableListener({ reader ->
                        if (activeSessionGeneration.get() == thisGeneration) {
                            processImage(reader)
                        } else {
                            reader.acquireLatestImage()?.close()
                        }
                    }, backgroundHandler)
                }

                if (surfaceTexture != null) {
                    val previewSizes = map?.getOutputSizes(SurfaceTexture::class.java)
                    val chosenPreviewSize = previewSizes?.firstOrNull { it.width <= 1920 && it.height <= 1080 }
                        ?: Size(1280, 720)
                    surfaceTexture.setDefaultBufferSize(chosenPreviewSize.width, chosenPreviewSize.height)
                    previewSurface?.release()
                    previewSurface = Surface(surfaceTexture)
                    Log.i(TAG, "[DIAGNOSTIC] Viewfinder surface configured at ${chosenPreviewSize.width}x${chosenPreviewSize.height}")
                }

                Log.i(TAG, "[DIAGNOSTIC] Invoking CameraManager.openCamera(id=$rearId)...")

                cameraManager.openCamera(rearId, object : CameraDevice.StateCallback() {
                    override fun onOpened(camera: CameraDevice) {
                        Log.i(TAG, "[DIAGNOSTIC] CameraDevice onOpened(id=${camera.id})")
                        cameraOpenCloseLock.release()

                        if (activeSessionGeneration.get() != thisGeneration) {
                            Log.w(TAG, "[DIAGNOSTIC] Stale onOpened callback discarded (gen $thisGeneration != ${activeSessionGeneration.get()})")
                            camera.close()
                            return
                        }

                        cameraDevice = camera
                        createCaptureSession(thisGeneration, onReady, onError)
                    }

                    override fun onDisconnected(camera: CameraDevice) {
                        Log.w(TAG, "[DIAGNOSTIC] CameraDevice onDisconnected(id=${camera.id})")
                        camera.close()
                        cameraOpenCloseLock.release()

                        if (activeSessionGeneration.get() == thisGeneration) {
                            cameraDevice = null
                            isOpening.set(false)
                            isReady.set(false)
                            onError("Camera disconnected.")
                        }
                    }

                    override fun onError(camera: CameraDevice, error: Int) {
                        Log.e(TAG, "[DIAGNOSTIC] CameraDevice onError(id=${camera.id}, code=$error)")
                        camera.close()
                        cameraOpenCloseLock.release()

                        if (activeSessionGeneration.get() == thisGeneration) {
                            cameraDevice = null
                            isOpening.set(false)
                            isReady.set(false)

                            val errorDescription = when (error) {
                                CameraDevice.StateCallback.ERROR_CAMERA_IN_USE ->
                                    "ERROR_CAMERA_IN_USE (1): Camera is already opened by another process or app."
                                CameraDevice.StateCallback.ERROR_MAX_CAMERAS_IN_USE ->
                                    "ERROR_MAX_CAMERAS_IN_USE (2): Maximum open camera device limit reached."
                                CameraDevice.StateCallback.ERROR_CAMERA_DISABLED ->
                                    "ERROR_CAMERA_DISABLED (3): Camera device disabled by system policy or security restrictions."
                                CameraDevice.StateCallback.ERROR_CAMERA_DEVICE ->
                                    "ERROR_CAMERA_DEVICE (4): Camera device encountered a fatal hardware failure."
                                CameraDevice.StateCallback.ERROR_CAMERA_SERVICE ->
                                    "ERROR_CAMERA_SERVICE (5): Android camera service encountered a fatal crash."
                                else -> "UNKNOWN_ERROR ($error)"
                            }
                            Log.e(TAG, "[DIAGNOSTIC] Camera open failed: $errorDescription")
                            onError(errorDescription)
                        }
                    }
                }, backgroundHandler)

            } catch (se: SecurityException) {
                cameraOpenCloseLock.release()
                if (activeSessionGeneration.get() == thisGeneration) {
                    isOpening.set(false)
                    isReady.set(false)
                    Log.e(TAG, "[DIAGNOSTIC] SecurityException: ${se.message}", se)
                    onError("SecurityException: Camera permission revoked or blocked by OS.")
                }
            } catch (e: Exception) {
                cameraOpenCloseLock.release()
                if (activeSessionGeneration.get() == thisGeneration) {
                    isOpening.set(false)
                    isReady.set(false)
                    Log.e(TAG, "[DIAGNOSTIC] Exception in openRearCamera: ${e.message}", e)
                    onError("Failed opening camera: ${e.message}")
                }
            }
        }
    }

    private fun createCaptureSession(
        expectedGen: Int,
        onReady: () -> Unit,
        onError: (String) -> Unit
    ) {
        val device = cameraDevice ?: return
        val reader = imageReader ?: return

        try {
            val surfaces = mutableListOf<Surface>(reader.surface)
            previewSurface?.let { surfaces.add(it) }

            Log.i(TAG, "[DIAGNOSTIC] Creating CameraCaptureSession with ${surfaces.size} surfaces...")
            device.createCaptureSession(surfaces, object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(session: CameraCaptureSession) {
                    if (activeSessionGeneration.get() != expectedGen) {
                        Log.w(TAG, "[DIAGNOSTIC] Stale onConfigured callback discarded.")
                        session.close()
                        return
                    }

                    Log.i(TAG, "[DIAGNOSTIC] CameraCaptureSession onConfigured successfully.")
                    captureSession = session
                    startRepeatingPreview(expectedGen, onFirstFrameReceived = {
                        if (activeSessionGeneration.get() == expectedGen) {
                            isOpening.set(false)
                            isReady.set(true)
                            onReady()
                        }
                    })
                }

                override fun onConfigureFailed(session: CameraCaptureSession) {
                    if (activeSessionGeneration.get() == expectedGen) {
                        Log.e(TAG, "[DIAGNOSTIC] CameraCaptureSession onConfigureFailed!")
                        isOpening.set(false)
                        isReady.set(false)
                        onError("Failed to configure Camera2 capture session")
                    }
                }
            }, backgroundHandler)
        } catch (e: Exception) {
            if (activeSessionGeneration.get() == expectedGen) {
                isOpening.set(false)
                isReady.set(false)
                Log.e(TAG, "[DIAGNOSTIC] Exception in createCaptureSession: ${e.message}", e)
                onError("Error creating session: ${e.message}")
            }
        }
    }

    private fun startRepeatingPreview(expectedGen: Int, onFirstFrameReceived: (() -> Unit)? = null) {
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

            var frameConfirmed = false
            session.setRepeatingRequest(previewBuilder.build(), object : CameraCaptureSession.CaptureCallback() {
                override fun onCaptureCompleted(
                    session: CameraCaptureSession,
                    request: CaptureRequest,
                    result: TotalCaptureResult
                ) {
                    if (activeSessionGeneration.get() != expectedGen) return

                    val sensorTimestamp = result.get(CaptureResult.SENSOR_TIMESTAMP)
                    if (!frameConfirmed && sensorTimestamp != null && sensorTimestamp > 0L) {
                        frameConfirmed = true
                        Log.i(TAG, "[DIAGNOSTIC] Live viewfinder confirmed receiving valid frames (ts=$sensorTimestamp ns).")
                        onFirstFrameReceived?.invoke()
                    }
                }

                override fun onCaptureFailed(
                    session: CameraCaptureSession,
                    request: CaptureRequest,
                    failure: CaptureFailure
                ) {
                    Log.w(TAG, "[DIAGNOSTIC] Preview frame dropped/failed: reason ${failure.reason}")
                }
            }, backgroundHandler)

            Log.i(TAG, "[DIAGNOSTIC] Continuous camera preview active with AE/AF/AWB enabled.")
        } catch (e: Exception) {
            Log.e(TAG, "[DIAGNOSTIC] Failed to start repeating preview: ${e.message}", e)
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
        val currentGen = activeSessionGeneration.get()

        if (session == null || reader == null || device == null || !isReady.get()) {
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
                    Log.i(TAG, "[DIAGNOSTIC] Camera2 hardware exposure started: sensor_ts=$timestamp ns")
                }

                override fun onCaptureCompleted(
                    session: CameraCaptureSession,
                    request: CaptureRequest,
                    result: TotalCaptureResult
                ) {
                    Log.i(TAG, "[DIAGNOSTIC] Camera2 still capture completed.")
                    startRepeatingPreview(currentGen)
                }

                override fun onCaptureFailed(
                    session: CameraCaptureSession,
                    request: CaptureRequest,
                    failure: CaptureFailure
                ) {
                    Log.e(TAG, "[DIAGNOSTIC] Capture failed: reason ${failure.reason}")
                    callback.onCaptureFailure("Capture failed: reason ${failure.reason}")
                    startRepeatingPreview(currentGen)
                }
            }, backgroundHandler)

        } catch (e: Exception) {
            Log.e(TAG, "[DIAGNOSTIC] Exception during takePicture: ${e.message}", e)
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

            val options = BitmapFactory.Options().apply { inJustDecodeBounds = false }
            val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
            if (bitmap == null) {
                currentCallback?.onCaptureFailure("Corrupt JPEG data: BitmapFactory failed to decode")
                return
            }

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
                    totalBrightness += 0.299 * r + 0.587 * g + 0.114 * b
                    sampleCount++
                }
            }
            val avgLuminance = if (sampleCount > 0) totalBrightness / sampleCount else 0.0
            Log.i(TAG, "[DIAGNOSTIC] Captured JPEG decoded: ${bitmap.width}x${bitmap.height}, Lum: $avgLuminance")
            bitmap.recycle()

            val md = MessageDigest.getInstance("SHA-256")
            val digest = md.digest(bytes)
            val sha256 = digest.joinToString("") { "%02x".format(it) }

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
            Log.e(TAG, "[DIAGNOSTIC] Failed processing image: ${e.message}", e)
            currentCallback?.onCaptureFailure("Failed saving image: ${e.message}")
        } finally {
            image?.close()
        }
    }

    private fun executeResourceDisposal() {
        Log.d(TAG, "[DIAGNOSTIC] Executing hardware resource disposal...")
        try {
            captureSession?.stopRepeating()
        } catch (e: Exception) {}
        try {
            captureSession?.close()
        } catch (e: Exception) {}
        captureSession = null

        try {
            cameraDevice?.close()
        } catch (e: Exception) {}
        cameraDevice = null

        try {
            imageReader?.close()
        } catch (e: Exception) {}
        imageReader = null

        try {
            previewSurface?.release()
        } catch (e: Exception) {}
        previewSurface = null

        stopBackgroundThread()
        Log.d(TAG, "[DIAGNOSTIC] Hardware resources disposed cleanly.")
    }

    fun close() {
        // 1. Immediately invalidate current generation and reset readiness
        activeSessionGeneration.incrementAndGet()
        isOpening.set(false)
        isReady.set(false)

        var acquired = false
        try {
            acquired = cameraOpenCloseLock.tryAcquire(2500, TimeUnit.MILLISECONDS)
            if (!acquired) {
                Log.w(TAG, "[DIAGNOSTIC] Lock busy in close(); scheduling deferred cleanup on background handler thread.")
                // Enqueue disposal to execute as soon as the in-flight background operation releases the lock
                backgroundHandler?.post {
                    try {
                        if (cameraOpenCloseLock.tryAcquire(3000, TimeUnit.MILLISECONDS)) {
                            try {
                                executeResourceDisposal()
                            } finally {
                                cameraOpenCloseLock.release()
                            }
                        } else {
                            Log.e(TAG, "[DIAGNOSTIC] Deferred cleanup timeout; forcing disposal.")
                            executeResourceDisposal()
                        }
                    } catch (e: Exception) {
                        Log.e(TAG, "[DIAGNOSTIC] Exception during deferred cleanup: ${e.message}")
                    }
                }
                return
            }

            executeResourceDisposal()
        } catch (e: Exception) {
            Log.e(TAG, "[DIAGNOSTIC] Exception in close(): ${e.message}")
        } finally {
            if (acquired) {
                cameraOpenCloseLock.release()
            }
        }
    }
}
