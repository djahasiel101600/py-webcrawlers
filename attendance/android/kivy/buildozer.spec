[app]
title = NIA Attendance Monitor
package.name = niaattendance
package.domain = com.yourname

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,txt

version = 1.0
requirements = python3,kivy,requests,urllib3,chardet,idna,certifi

orientation = portrait

[buildozer]
log_level = 2

# Android specific
[app:android]
api = 33
minapi = 21
android.permissions = INTERNET,ACCESS_NETWORK_STATE,WAKE_LOCK
android.allow_backup = True
android.accept_sdk_license = false

# iOS specific (if needed later)
[app:ios]

# Windows specific (for testing)
[app:windows]
