package com.multicamera.capture.network

import android.os.Handler
import android.os.Looper
import android.util.Log
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.math.min
import kotlin.math.pow

interface ImageUploadCallback {
    fun onUploadSuccess(response: JSONObject)
    fun onUploadFailure(error: String)
}

/**
 * Uploads uncompressed images over HTTP multipart with SHA-256 integrity verification
 * and exponential backoff retry for transient network dropouts.
 */
class ImageUploader {

    companion object {
        private const val TAG = "ImageUploader"
        private const val MAX_RETRIES = 4
        private const val BASE_BACKOFF_MS = 1000L
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    private val mainHandler = Handler(Looper.getMainLooper())

    fun uploadImage(
        host: String,
        port: Int,
        sessionId: String,
        cameraId: String,
        captureRequestId: String,
        imageFile: File,
        sha256Checksum: String,
        metadata: JSONObject,
        callback: ImageUploadCallback
    ) {
        attemptUploadWithRetry(
            host = host,
            port = port,
            sessionId = sessionId,
            cameraId = cameraId,
            captureRequestId = captureRequestId,
            imageFile = imageFile,
            sha256Checksum = sha256Checksum,
            metadata = metadata,
            attempt = 0,
            callback = callback
        )
    }

    private fun attemptUploadWithRetry(
        host: String,
        port: Int,
        sessionId: String,
        cameraId: String,
        captureRequestId: String,
        imageFile: File,
        sha256Checksum: String,
        metadata: JSONObject,
        attempt: Int,
        callback: ImageUploadCallback
    ) {
        val url = "http://$host:$port/api/upload"

        if (!imageFile.exists() || imageFile.length() == 0L) {
            mainHandler.post { callback.onUploadFailure("Image file is missing or zero bytes") }
            return
        }

        val fileBody = imageFile.asRequestBody("image/jpeg".toMediaTypeOrNull())

        val requestBody = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("session_id", sessionId)
            .addFormDataPart("camera_id", cameraId)
            .addFormDataPart("capture_request_id", captureRequestId)
            .addFormDataPart("sha256_checksum", sha256Checksum)
            .addFormDataPart("metadata_json", metadata.toString())
            .addFormDataPart("file", imageFile.name, fileBody)
            .build()

        val request = Request.Builder()
            .url(url)
            .post(requestBody)
            .build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                Log.w(TAG, "Upload attempt $attempt failed: ${e.message}")
                handleRetryOrFailure(
                    host, port, sessionId, cameraId, captureRequestId,
                    imageFile, sha256Checksum, metadata, attempt,
                    "Network failure: ${e.message}", callback
                )
            }

            override fun onResponse(call: Call, response: Response) {
                val bodyStr = response.body?.string() ?: ""
                if (response.isSuccessful) {
                    try {
                        val json = JSONObject(bodyStr)
                        mainHandler.post { callback.onUploadSuccess(json) }
                    } catch (e: Exception) {
                        mainHandler.post { callback.onUploadSuccess(JSONObject()) }
                    }
                } else if (response.code in 500..599 || response.code == 408) {
                    // Server side transient error: eligible for retry
                    Log.w(TAG, "Server error (${response.code}), retrying...")
                    handleRetryOrFailure(
                        host, port, sessionId, cameraId, captureRequestId,
                        imageFile, sha256Checksum, metadata, attempt,
                        "Server HTTP ${response.code}: $bodyStr", callback
                    )
                } else {
                    // Client error (e.g. 422 checksum mismatch, 403 unregistered) -> DO NOT retry blindly
                    Log.e(TAG, "Permanent upload rejection (${response.code}): $bodyStr")
                    mainHandler.post {
                        callback.onUploadFailure("Server HTTP ${response.code}: $bodyStr")
                    }
                }
            }
        })
    }

    private fun handleRetryOrFailure(
        host: String,
        port: Int,
        sessionId: String,
        cameraId: String,
        captureRequestId: String,
        imageFile: File,
        sha256Checksum: String,
        metadata: JSONObject,
        attempt: Int,
        lastError: String,
        callback: ImageUploadCallback
    ) {
        if (attempt < MAX_RETRIES) {
            val backoffMs = (BASE_BACKOFF_MS * (2.0.pow(attempt.toDouble()))).toLong()
            val delay = min(backoffMs, 8000L)
            Log.d(TAG, "Scheduling retry attempt ${attempt + 1} in ${delay}ms...")
            mainHandler.postDelayed({
                attemptUploadWithRetry(
                    host, port, sessionId, cameraId, captureRequestId,
                    imageFile, sha256Checksum, metadata, attempt + 1, callback
                )
            }, delay)
        } else {
            mainHandler.post {
                callback.onUploadFailure("Max retries ($MAX_RETRIES) exceeded. Last error: $lastError")
            }
        }
    }
}
