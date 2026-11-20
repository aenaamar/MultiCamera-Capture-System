package com.multicamera.capture

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ProtocolCompatibilityTest {

    @Test
    fun testRegistrationPayloadFormat() {
        val cameraId = "camera_01"
        val deviceModel = "Pixel 7 Pro"
        val batteryLevel = 88
        val sensorInfo = JSONObject().apply {
            put("lens_facing", "BACK")
            put("hardware_level", 3)
        }

        val regObj = JSONObject().apply {
            put("type", "device_register")
            put("camera_id", cameraId)
            put("device_name", deviceModel)
            put("battery_level", batteryLevel)
            put("sensor_info", sensorInfo)
        }

        assertEquals("device_register", regObj.getString("type"))
        assertEquals("camera_01", regObj.getString("camera_id"))
        assertEquals("Pixel 7 Pro", regObj.getString("device_name"))
        assertEquals(88, regObj.getInt("battery_level"))
        assertEquals("BACK", regObj.getJSONObject("sensor_info").getString("lens_facing"))
    }

    @Test
    fun testHeartbeatPayloadFormat() {
        val statusObj = JSONObject().apply {
            put("type", "device_status")
            put("camera_id", "camera_02")
            put("state", "READY")
            put("battery_level", 75)
        }

        assertEquals("device_status", statusObj.getString("type"))
        assertEquals("camera_02", statusObj.getString("camera_id"))
        assertEquals("READY", statusObj.getString("state"))
        assertEquals(75, statusObj.getInt("battery_level"))
    }
}
