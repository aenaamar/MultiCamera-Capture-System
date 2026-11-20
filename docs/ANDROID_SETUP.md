# Android Companion Application Setup and Deployment Guide

## 1. Overview
The Android Companion Application (`android_app`) turns Android smartphones into coordinated, remote-triggered camera nodes. It connects to the Windows laptop server over local Wi-Fi, registers device telemetry and Camera2 sensor capabilities, responds to remote capture triggers, and automatically transfers uncompressed JPEGs verified with SHA-256 checksums.

---

## 2. JDK & Android Gradle Plugin (AGP) Requirements

- **Android Gradle Plugin (AGP):** `8.2.2`
- **Gradle Version:** `8.5`
- **Minimum JDK Requirement:** **JDK 17 or JDK 21**
  > **Note on your system:** If your command line default is JDK 11 (`javac 11.0.30`), Android Studio provides its own built-in JetBrains Runtime (JDK 17/21). Android Studio will compile and build the APK without needing to alter your system-wide JDK.

---

## 3. How to Open and Build in Android Studio

1. Open **Android Studio**.
2. Select **File -> Open...** and browse to:
   ```
   D:\MultiCamera_Capture_System\android_app
   ```
3. Wait for Gradle Sync to complete.
4. Verify Gradle JDK setting:
   - Go to **File -> Settings -> Build, Execution, Deployment -> Build Tools -> Gradle**.
   - Under **Gradle JDK**, ensure **Embedded JDK (version 17 or 21)** or an external JDK 17+ is selected.
5. Click **Build -> Make Project** (or `Ctrl+F9`).
6. To assemble the debug APK:
   - In Android Studio, go to the **Gradle** side panel:
     `MultiCameraCapture -> app -> Tasks -> build -> assembleDebug`.
   - The generated APK will be at:
     `android_app/app/build/outputs/apk/debug/app-debug.apk`.

---

## 4. How to Install on a Real Android Phone

1. **Enable Developer Options & USB Debugging on your phone:**
   - Go to **Settings -> About Phone**.
   - Tap **Build Number** 7 times until you see "You are now a developer!".
   - Go to **Settings -> System -> Developer Options** and enable **USB Debugging**.
2. **Connect phone via USB:**
   - Plug the phone into your laptop.
   - Authorize the computer on the phone prompt ("Always allow from this computer").
3. **Install from Android Studio:**
   - In the top toolbar, select your connected device from the device dropdown.
   - Click the green **Run (Play)** button (`Shift+F10`).
   - The app will install and open immediately.
4. **Alternatively install via ADB from command prompt:**
   ```bash
   adb install -r android_app/app/build/outputs/apk/debug/app-debug.apk
   ```

---

## 5. Connecting and Operating the App

1. Ensure the phone is connected to the **same Wi-Fi network** as the Windows laptop.
2. Grant the camera permission prompt when the app opens.
3. Configure settings:
   - **Camera Identifier:** Assign each phone a distinct ID (`camera_01`, `camera_02`, `camera_03`, `camera_04`, etc.).
   - **Laptop Server IP Address:** Enter the IP shown on your laptop app dashboard (e.g. `192.168.1.6`).
   - **Server Port:** `8000`.
4. Tap **Connect to Laptop**.
5. The status indicator will turn **GREEN (Status: Registered - READY)**.
6. The phone is now standing by for remote captures triggered from the laptop.
