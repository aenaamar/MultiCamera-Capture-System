package com.multicamera.capture.device

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.os.BatteryManager
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject

/**
 * Handles device inspection: model info, battery level, and Camera2 sensor capabilities.
 */
class DeviceInspector(private val context: Context) {

    val deviceModel: String
        get() = "${Build.MANUFACTURER} ${Build.MODEL}"

    val batteryLevel: Int
        get() {
            return try {
                val batteryStatus: Intent? = context.registerReceiver(
                    null,
                    IntentFilter(Intent.ACTION_BATTERY_CHANGED)
                )
                val level = batteryStatus?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
                val scale = batteryStatus?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
                if (level >= 0 && scale > 0) {
                    ((level.toFloat() / scale.toFloat()) * 100).toInt()
                } else {
                    -1
                }
            } catch (e: Exception) {
                -1
            }
        }

    fun getCameraCharacteristicsJson(): JSONObject {
        val root = JSONObject()
        try {
            val cameraManager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val cameraIds = cameraManager.cameraIdList

            // Find main rear camera
            var rearCameraId: String? = null
            for (id in cameraIds) {
                val chars = cameraManager.getCameraCharacteristics(id)
                val facing = chars.get(CameraCharacteristics.LENS_FACING)
                if (facing == CameraCharacteristics.LENS_FACING_BACK) {
                    rearCameraId = id
                    break
                }
            }

            if (rearCameraId != null) {
                val chars = cameraManager.getCameraCharacteristics(rearCameraId)
                root.put("selected_camera_id", rearCameraId)
                root.put("lens_facing", "BACK")

                // Focal lengths
                val focalLengths = chars.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)
                if (focalLengths != null) {
                    val focalArray = JSONArray()
                    for (f in focalLengths) {
                        focalArray.put(f.toDouble())
                    }
                    root.put("available_focal_lengths", focalArray)
                }

                // Sensor Physical Size
                val sensorSize = chars.get(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE)
                if (sensorSize != null) {
                    val sizeObj = JSONObject()
                    sizeObj.put("width_mm", sensorSize.width.toDouble())
                    sizeObj.put("height_mm", sensorSize.height.toDouble())
                    root.put("sensor_physical_size", sizeObj)
                }

                // Sensor Pixel Array Size
                val pixelArraySize = chars.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE)
                if (pixelArraySize != null) {
                    val pixelObj = JSONObject()
                    pixelObj.put("width", pixelArraySize.width)
                    pixelObj.put("height", pixelArraySize.height)
                    root.put("pixel_array_size", pixelObj)
                }

                // Hardware level
                val hwLevel = chars.get(CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL)
                root.put("hardware_level", hwLevel ?: -1)
            }
        } catch (e: Exception) {
            root.put("error", e.message ?: "Failed querying Camera2")
        }
        return root
    }
}
