plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.vivatlas.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.vivatlas.app"
        minSdk = 24
        targetSdk = 34
        versionCode = 6
        versionName = "1.5"
    }

    // Google's "dependency metadata" block, which the Android Gradle plugin puts
    // into every signed APK by default. It is an opaque blob meant for Play, and
    // F-Droid refuses APKs that carry it, so it stays out.
    dependenciesInfo {
        includeInApk = false
        includeInBundle = false
    }

    // Release signing comes from the environment, which is how CI hands it in
    // (.github/workflows/android.yml decodes the keystore from a secret). With
    // nothing set — a local build — the release APK is simply left unsigned, so
    // `assembleRelease` still works on any machine; it just isn't installable
    // until signed. The key is what lets updates install over each other, so it
    // must be the same one every release: keep it out of the repo, forever.
    val keystorePath = System.getenv("ANDROID_KEYSTORE_FILE")
    signingConfigs {
        if (keystorePath != null) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("KEYSTORE_PASSWORD")
                keyAlias = System.getenv("KEY_ALIAS")
                keyPassword = System.getenv("KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        debug {
            // Installed side-by-side with a release build; talks to the dev server.
            applicationIdSuffix = ".debug"
            isDebuggable = true
        }
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            if (keystorePath != null) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        // MainActivity reads BuildConfig.DEBUG to gate WebView remote debugging.
        buildConfig = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.core:core-splashscreen:1.0.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.11.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    // QR scanning for "sign in by code". ZXing rather than ML Kit: it carries its own
    // decoder, so a self-hosted app doesn't gain a Google Play services dependency.
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
}
