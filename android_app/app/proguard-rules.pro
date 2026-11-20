# Add project specific ProGuard rules here.
# You can control the set of applied configuration files using the
# proguardFiles setting in build.gradle.kts.

# Keep OkHttp classes
-dontwarn okhttp3.**
-keep class okhttp3.** { *; }
-keep interface okhttp3.** { *; }

# Keep org.json classes
-keep class org.json.** { *; }

# Keep application model classes
-keep class com.multicamera.capture.** { *; }
