#!/usr/bin/env bash
# きこえる地図 Android アプリ（TWA）をビルドする
#   使い方: android/build.sh [versionName] [versionCode]
#   成果物: android/dist/kikoeru-map-<version>.apk（端末へ直接インストール用）と .aab（Google Play 用）
set -euo pipefail

cd "$(dirname "$0")"
version_name="${1:-1.0.0}"
version_code="${2:-1}"
image="kikoeru-android-build"
uid="$(id -u)"; gid="$(id -g)"

docker build -q -t "$image" . > /dev/null

run() {
  docker run --rm -v "$PWD":/project -v kikoeru-android-gradle:/root/.gradle "$image" bash -c "$1"
}

# 署名鍵は初回だけ作る。これを失うと Google Play で更新できなくなるので必ずバックアップする
if [ ! -f keystore/release.jks ]; then
  mkdir -p keystore
  pass="$(head -c 24 /dev/urandom | base64 | tr -d '/+=')"
  run "keytool -genkeypair -v -keystore keystore/release.jks -alias kikoeru -keyalg RSA -keysize 4096 \
        -validity 36500 -storepass '$pass' -keypass '$pass' \
        -dname 'CN=Kikoeru Map, O=CreamDevelopers, C=JP' > /dev/null 2>&1 && chown -R $uid:$gid keystore"
  cat > keystore.properties <<PROPS
storeFile=keystore/release.jks
storePassword=$pass
keyAlias=kikoeru
keyPassword=$pass
PROPS
  chmod 600 keystore.properties keystore/release.jks
  echo "署名鍵を作成しました: android/keystore/release.jks（パスワードは android/keystore.properties）"
fi

run "gradle --no-daemon -q -PversionName=$version_name -PversionCode=$version_code assembleRelease bundleRelease \
     && mkdir -p dist \
     && cp app/build/outputs/apk/release/app-release.apk dist/kikoeru-map-$version_name.apk \
     && cp app/build/outputs/bundle/release/app-release.aab dist/kikoeru-map-$version_name.aab \
     && chown -R $uid:$gid dist app/build .gradle 2>/dev/null; true"

pass="$(grep '^storePassword=' keystore.properties | cut -d= -f2-)"
fp="$(run "keytool -list -v -keystore keystore/release.jks -alias kikoeru -storepass '$pass'" | grep 'SHA256:' | awk '{print $2}')"
echo
echo "ビルド完了:"
ls -lh dist/kikoeru-map-"$version_name".*
echo
echo "署名証明書の SHA-256: $fp"
echo "→ サーバーの .env に ANDROID_CERT_SHA256=$fp を設定すると、アプリでアドレスバーが消えます"
