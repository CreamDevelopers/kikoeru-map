# Android SDK と Gradle を入れたビルド専用イメージ（ホストに Java/SDK を入れずに済ませる）
FROM eclipse-temurin:17-jdk

ENV ANDROID_HOME=/opt/android-sdk \
    GRADLE_HOME=/opt/gradle
ENV PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$GRADLE_HOME/bin

RUN apt-get update \
 && apt-get install -y --no-install-recommends unzip wget \
 && rm -rf /var/lib/apt/lists/*

RUN wget -q https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip -O /tmp/cmdline.zip \
 && mkdir -p $ANDROID_HOME/cmdline-tools \
 && unzip -q /tmp/cmdline.zip -d $ANDROID_HOME/cmdline-tools \
 && mv $ANDROID_HOME/cmdline-tools/cmdline-tools $ANDROID_HOME/cmdline-tools/latest \
 && rm /tmp/cmdline.zip \
 && yes | sdkmanager --licenses > /dev/null \
 && sdkmanager "platforms;android-35" "build-tools;35.0.0" > /dev/null

RUN wget -q https://services.gradle.org/distributions/gradle-8.10.2-bin.zip -O /tmp/gradle.zip \
 && unzip -q /tmp/gradle.zip -d /opt \
 && mv /opt/gradle-8.10.2 $GRADLE_HOME \
 && rm /tmp/gradle.zip

WORKDIR /project
