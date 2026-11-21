package com.multicamera.capture.device

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/**
 * Handles device inspection: unique device identity, model info, battery level,
 * Wi-Fi MAC (if accessible), and Camera2 sensor capabilities.
 *
 * ## Unique Device ID
 * On first launch, a random UUID4 is generated and stored in SharedPreferences.
 * This ID survives app restarts and network reconnects.
 * Two phones of the same brand and model will always have different device IDs.
 *
 * The ID is NOT derived from the IMEI, serial number, or any other sensitive
 * hardware identifier — it is a randomly generated application-level identity.
 */
class DeviceInspector(private val context: Context) {

    companion object {
        private const val TAG = "DeviceInspector"
        private const val PREFS_NAME = "multicamera_device_prefs"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_CAMERA_SLOT = "camera_slot"
    }

    // -------------------------------------------------------------------------
    // Unique Device ID (UUID4, generated once, stored persistently)
    // -------------------------------------------------------------------------

    /**
     * Returns the persistent device UUID for this phone.
     * Generated once on first run; never changes unless app data is cleared.
     */
    val deviceId: String
        get() = getOrCreateDeviceId()

    private fun getOrCreateDeviceId(): String {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        var id = prefs.getString(KEY_DEVICE_ID, null)
        if (id.isNullOrBlank()) {
            id = UUID.randomUUID().toString()
            prefs.edit().putString(KEY_DEVICE_ID, id).apply()
            Log.i(TAG, "Generated new device_id: $id")
        } else {
            Log.d(TAG, "Loaded existing device_id: $id")
        }
        return id
    }

    // -------------------------------------------------------------------------
    // Camera slot suggestion (e.g. camera_01)
    // -------------------------------------------------------------------------

    /**
     * Returns the user's saved camera slot name (default: "camera_01").
     * The user can change it in the UI; the new value is persisted on save.
     */
    fun getSavedCameraSlot(): String {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        return prefs.getString(KEY_CAMERA_SLOT, "camera_01") ?: "camera_01"
    }

    fun saveCameraSlot(slot: String) {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().putString(KEY_CAMERA_SLOT, slot.trim()).apply()
    }

    // -------------------------------------------------------------------------
    // Hardware / OS info
    // -------------------------------------------------------------------------

    /** Returns the phone model in "Manufacturer Model" format (e.g. "Samsung Galaxy S22"). */
    val deviceModel: String
        get() = "${Build.MANUFACTURER} ${Build.MODEL}"

    val manufacturer: String
        get() = Build.MANUFACTURER

    val model: String
        get() = Build.MODEL

    /**
     * OS version string in human-readable form, e.g. "Android 14 (API 34)".
     */
    val osVersion: String
        get() = "Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})"

    // -------------------------------------------------------------------------
    // Battery
    // -------------------------------------------------------------------------

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

    // -------------------------------------------------------------------------
    // Wi-Fi MAC address
    // -------------------------------------------------------------------------

    /**
     * Attempts to read the Wi-Fi MAC address.
     *
     * Important limitations:
     * - Android 10+ randomises the MAC per network — the value returned here
     *   is the *factory* MAC only if the system still exposes it.
     * - On most Android 10+ devices this returns "02:00:00:00:00:00" or null.
     * - We collect it as supplementary information only; we never use it as
     *   a primary identity key because of randomisation.
     *
     * Requires ACCESS_WIFI_STATE permission.
     */
    val wifiMacAddress: String?
        get() {
            return try {
                @Suppress("DEPRECATION")
                val wifiManager = context.applicationContext
                    .getSystemService(Context.WIFI_SERVICE) as WifiManager
                @Suppress("DEPRECATION")
                val mac = wifiManager.connectionInfo?.macAddress
                // Filter out the randomised placeholder value
                if (mac != null && mac != "02:00:00:00:00:00") mac else null
            } catch (e: Exception) {
                Log.d(TAG, "MAC address not available: ${e.message}")
                null
            }
        }

    // -------------------------------------------------------------------------
    // Complete device info bundle
    // -------------------------------------------------------------------------

    /**
     * Returns all device information as a JSONObject suitable for the
     * registration payload.
     */
    fun getDeviceInfoJson(): JSONObject {
        return JSONObject().apply {
            put("device_id", deviceId)
            put("manufacturer", manufacturer)
            put("model", model)
            put("device_name", deviceModel)
            put("os_version", osVersion)
            put("battery_level", batteryLevel)
            val mac = wifiMacAddress
            if (mac != null) put("mac_address", mac)
        }
    }

    // -------------------------------------------------------------------------
    // Camera2 sensor characteristics
    // -------------------------------------------------------------------------

    fun getCameraCharacteristicsJson(): JSONObject {
        val root = JSONObject()
        try {
            val cameraManager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val cameraIds = cameraManager.cameraIdList

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

                val focalLengths = chars.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)
                if (focalLengths != null) {
                    val focalArray = JSONArray()
                    for (f in focalLengths) focalArray.put(f.toDouble())
                    root.put("available_focal_lengths", focalArray)
                }

                val sensorSize = chars.get(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE)
                if (sensorSize != null) {
                    root.put("sensor_physical_size", JSONObject().apply {
                        put("width_mm", sensorSize.width.toDouble())
                        put("height_mm", sensorSize.height.toDouble())
                    })
                }

                val pixelArraySize = chars.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE)
                if (pixelArraySize != null) {
                    root.put("pixel_array_size", JSONObject().apply {
                        put("width", pixelArraySize.width)
                        put("height", pixelArraySize.height)
                    })
                }

                val hwLevel = chars.get(CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL)
                root.put("hardware_level", hwLevel ?: -1)
            }
        } catch (e: Exception) {
            root.put("error", e.message ?: "Failed querying Camera2")
        }
        return root
    }
}
