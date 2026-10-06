# Android App Implementation Plan (Personal APK)

Target: Get a runnable Android APK on your personal phone for personal use (before Play Store considerations). 
The markdown/git repo stays the source of truth. The Python code is a permanent build-time toolchain (never ships in APK).

## Current State

- `../../../../Github/cookbook_for_idiots/core` module exists with `Tables.kt` (generated from Python) - contains vocabulary tables
- `../../../../Github/cookbook_for_idiots/app` module is currently a CLI template (Kotlin JVM app plugin) - needs conversion to Android
- Python reference implementation is complete (`../../../../Github/cookbook_for_idiots/scripts/recipe.py`, `units.py`, `classify.py`, `export_tables.py`)
- Golden test harness exists (15 fixtures, 204 Python tests total)
- `../../../../Github/cookbook_for_idiots/settings.gradle.kts` only includes `app` - needs to include `core` as well

## Essential Decisions (Confirmed)

| Decision | Rationale |
|---|---|
| Extractor core | Full Kotlin + Jsoup (no Python on device). No NDK, no 16KB page-size issues. |
| Source of truth | Markdown files in git repo. |
| On-device only | No server, no network calls beyond user-initiated recipe page fetches. |
| Python's role | Permanent build-time toolchain. Generates `Tables.kt` from Python source. Never in APK. |
| Git integration | SAF (Storage Access Framework) import/export only - no embedded JGit. |
| Review-first UX | Review queue is the core feature (conflict detection, conversion notes, lint findings). |

---

## Step-by-Step Implementation

### STEP 1: Fix Module Structure

1. **Update settings.gradle.kts** to include core module:
```kotlin
rootProject.name = "cookbook_for_idiots"
include("core", "app")
```

2. **Create core/build.gradle.kts** (pure Kotlin JVM module, no Android deps):
```kotlin
plugins {
    alias(libs.plugins.kotlin.jvm)
}

repositories {
    mavenCentral()
}

dependencies {
    implementation(libs.jsoup)
    testImplementation(kotlin("test"))
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.2")
}

tasks.test {
    useJUnitPlatform()
}

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(21)
    }
}
```

3. **Update gradle/libs.versions.toml** to add dependencies:
```toml
[versions]
jsoup = "1.17.2"

[libraries]
jsoup = { module = "org.jsoup:jsoup", version.ref = "jsoup" }

[plugins]
kotlin-jvm = { id = "org.jetbrains.kotlin.jvm", version = "2.4.10" }
```

---

### STEP 2: Convert App Module to Android

1. **Update app/build.gradle.kts** for Android + Compose:

```kotlin
plugins {
    id("com.android.application")
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    id("com.google.devtools.ksp")
}

android {
    namespace = "no.cookbook"
    compileSdk = 35

    defaultConfig {
        applicationId = "no.cookbook.recipes"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
        debug {
            isDebuggable = true
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }

    kotlinOptions {
        jvmTarget = "21"
    }

    buildFeatures {
        compose = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    implementation(project(":core"))
    
    // Compose
    implementation(platform("androidx.compose:compose-bom:2024.10.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")
    
    // Navigation
    implementation("androidx.navigation:navigation-compose:2.8.3")
    
    // Networking
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    
    // Room
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")
    
    // Permissions/Document API
    implementation("androidx.documentfile:documentfile:1.0.1")
    
    // Core
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    
    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
    
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
    androidTestImplementation(platform("androidx.compose:compose-bom:2024.10.01"))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
}
```

2. **Update gradle/libs.versions.toml** to add Android/Compose/KSP plugins:
```toml
[versions]
agp = "8.7.2"
kotlin = "2.0.21"
ksp = "2.0.21-1.0.25"
jsoup = "1.17.2"

[libraries]
jsoup = { module = "org.jsoup:jsoup", version.ref = "jsoup" }

[plugins]
android-application = { id = "com.android.application", version.ref = "agp" }
kotlin-android = { id = "org.jetbrains.kotlin.android", version.ref = "kotlin" }
kotlin-compose = { id = "org.jetbrains.kotlin.plugin.compose", version.ref = "kotlin" }
kotlin-jvm = { id = "org.jetbrains.kotlin.jvm", version.ref = "kotlin" }
ksp = { id = "com.google.devtools.ksp", version.ref = "ksp" }
```

3. **Create minimal Android project files in app/:**
- `app/src/main/AndroidManifest.xml`
- `app/src/main/res/values/strings.xml`, `themes.xml`, `colors.xml` (minimal)
- `app/proguard-rules.pro` (optional minimal)

**AndroidManifest.xml:**
```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.INTERNET" />
    <application
        android:allowBackup="true"
        android:icon="@mipmap/ic_launcher"
        android:label="@string/app_name"
        android:roundIcon="@mipmap/ic_launcher_round"
        android:supportsRtl="true"
        android:theme="@style/Theme.Cookbook">
        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:theme="@style/Theme.Cookbook">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
            <intent-filter>
                <action android:name="android.intent.action.SEND" />
                <category android:name="android.intent.category.DEFAULT" />
                <data android:mimeType="text/plain" />
            </intent-filter>
        </activity>
    </application>
</manifest>
```

4. **Replace CLI App.kt with Compose MainActivity** in appropriate package (e.g., `no.cookbook/MainActivity.kt`).

---

### STEP 3: Port Core Extraction Logic to Kotlin (Core Module)

You need to port these pure functions from Python to Kotlin in `../../../../Github/cookbook_for_idiots/core/src/main/kotlin/no/cookbook`. Use Tables.kt as-is (it's already generated).

**Port in this exact order (cheapest first):**

#### 3a. Units (`units.py` → `Units.kt`) 
Port: `normalize_line()`, `normalize_text()`, conversion logic, fraction parsing, temperature ranges. (399 lines, self-contained). Reference: `units.py:421-433` for normalize functions.

#### 3b. Classification (`classify.py` → `Classify.kt`)
Port: `classify_category()` using Tables.CATEGORY_KEYWORDS, Tables.NEEDS_BOUNDARY etc. (135 lines). Reference: `classify.py:111`.

#### 3c. Markdown (parse/render/lint)
Python writes markdown; Kotlin needs to parse existing markdown files (for SAF sync) and render extracted recipes. Also port `render()` and `lint()` from `recipe.py`. 
- `render()`: `recipe.py:668`
- `lint()`, `lint_provenance()`: `recipe.py:989, 1064`

#### 3d. Extractor (`recipe.py` → `Extractor.kt`)
Port `extract(html)` and supporting pure logic. This is the bulk of work (~700-800 lines of core extraction logic). Key areas:
- JSON-LD parsing (use Jsoup + basic JSON parsing; or lightweight approach)
- Microdata extraction
- Markup/headings fallback tier
- Affiliate link unwrapping, junk filtering, heading normalization

**Critical:** Use the golden fixtures as your acceptance test. Do NOT hand-translate all 204 Python tests - write golden tests against fixtures instead.

---

### STEP 4: Generate Tables.kt (Verify/Regenerate)

Tables.kt exists and is generated. If you modify Python tables, regenerate:
```bash
cd /Users/hanniko/Github/cookbook_for_idiots
.venv/bin/python scripts/export_tables.py
```

Verify it's in sync with CI check:
```bash
.venv/bin/python scripts/export_tables.py --check
```

---

### STEP 5: Write Core Module Tests

Create `../../../../Github/cookbook_for_idiots/core/src/test/kotlin/no/cookbook` with golden-based tests. **Recommended approach:**

1. **Golden fixture tests** - iterate over all 15 fixture HTML files, extract with Kotlin, compare against golden JSON. This catches divergences vs Python reference.
2. Add targeted unit tests for tricky cases (temperature ranges, mixed fractions, HowToSection handling, redundant imperial+metric) with clear comments explaining the rule.

Do NOT port the ~40 CLI-only Python tests. Focus on the pure extraction logic.

---

### STEP 6: Build & Run on Personal Phone

### Prerequisites
- Android Studio (recommended) OR Android SDK + build tools
- ADB enabled on your phone (Developer options > USB debugging)
- Java 21 (as configured)

### Build Debug APK
```bash
cd /Users/hanniko/Github/cookbook_for_idiots
./gradlew :app:assembleDebug
```

APK location: `app/build/outputs/apk/debug/app-debug.apk`

### Install on Personal Phone
1. Connect phone via USB with ADB debugging enabled
2. Install: `adb install app/build/outputs/apk/debug/app-debug.apk`
3. Or just copy APK to phone and install (allow "Install from unknown sources" for this app)

### Build Release APK (smaller, for personal use)
```bash
./gradlew :app:assembleRelease
```
APK: `app/build/outputs/apk/release/app-release.apk` (unsigned by default for personal use; fine to sideload)

---

### STEP 7: Implement Minimal App UI (v1 for Personal Use)

Focus on the essentials to be usable:

1. **Share Intent** - Primary entry: share a recipe URL from browser → app extracts it
2. **URL bar/manual input** - Paste URL directly
3. **Extraction screen** - Show progress, then navigate to review
4. **Review queue** - Show extracted recipe, conversion notes, lint findings, conflicts. Allow edit before save.
5. **Recipe list** - Browse saved recipes (from Room DB/index)
6. **SAF sync** - Let user pick a folder (ACTION_OPEN_DOCUMENT_TREE) to read/write markdown files. Treat markdown files as authoritative source.
7. **Offline cook mode** - View saved recipes offline

**Permissions:** Only `INTERNET` (for fetching recipe pages user explicitly shares/enters). No storage permissions needed with SAF.

---

## Verification Checklist (Before Using)

- [ ] `./gradlew :core:test` passes all core extraction tests
- [ ] `./gradlew :app:assembleDebug` builds successfully
- [ ] APK installs on personal phone
- [ ] Share a recipe URL from browser → app receives it
- [ ] Extract a known fixture URL/page → produces expected markdown structure
- [ ] Lint/typecheck passes: `./gradlew :core:build :app:build` (or run lint if configured)
- [ ] No secrets logged; no crash reporters sending URLs to third parties

---

## Notes & Gotchas

- **Python toolchain is required during development** if you ever regenerate Tables.kt or run Python tests. But not needed at runtime in the APK.
- **Jsoup vs libxml2**: Some HTML normalization differences are expected. Resolve via golden fixtures (update golden if the Kotlin behavior is actually better/correct).
- **User-initiated fetching only**: Never batch-fetch or background-fetch. This avoids Play policy issues and is fine for personal use.
- **WebView (JS-render)** is explicitly deferred to v2 - skip for now.
- **Keep Python tests passing** - they guard the reference implementation. Don't modify them unless fixing the reference for a real issue with justification.

