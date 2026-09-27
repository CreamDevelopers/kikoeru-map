import java.util.Properties

plugins {
    id("com.android.application")
}

// 署名情報は Git に入れない keystore.properties から読む（build.sh が初回に生成する）
val keystoreProps = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

val siteHost = "map.kikoeru.org"

android {
    namespace = "org.kikoeru.map"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.kikoeru.map"
        minSdk = 21
        targetSdk = 35
        versionCode = (findProperty("versionCode") as String?)?.toInt() ?: 1
        versionName = (findProperty("versionName") as String?) ?: "1.0.0"

        manifestPlaceholders["hostName"] = siteHost
        manifestPlaceholders["launchUrl"] = "https://$siteHost/?source=twa"
        resValue("string", "launchUrl", "https://$siteHost/?source=twa")
        resValue(
            "string",
            "assetStatements",
            """[{"relation":["delegate_permission/common.handle_all_urls"],"target":{"namespace":"web","site":"https://$siteHost"}}]""",
        )
    }

    signingConfigs {
        create("release") {
            if (keystoreProps.isNotEmpty()) {
                storeFile = rootProject.file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}

dependencies {
    implementation("com.google.androidbrowserhelper:androidbrowserhelper:2.5.0")
}
