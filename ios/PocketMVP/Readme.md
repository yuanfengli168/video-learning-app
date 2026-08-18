# Readme




## How to use command to rebuild Xcode projects
```
Questions: tell me the command to do this: '1. Terminate and relaunch the iOS app on your simulator (the binary has the new code, but the running process is the old one)'


Answers:
How to terminate and relaunch the iOS app
Option A: From the terminal (fastest)
CODE HERE: 
# 1. Terminate the running app
xcrun simctl terminate booted com.shoothigh.pocketmvp

# 2. Relaunch it (loads the new binary)
xcrun simctl launch booted com.shoothigh.pocketmvp

The output will show the new PID: `com.shoothigh.pocketmvp: 65620`

Option B: From the iOS sim GUI
Swipe up from the bottom of the simulator (or press Cmd+Shift+H twice fast) to go home
Swipe up on the app's preview card to kill it (or long-press → tap the red - badge)
Tap the PocketMVP icon on the home screen to relaunch
Verify the new binary is loaded
`
# Check the installed binary's modification time
ls -la "$(xcrun simctl get_app_container booted com.shoothigh.pocketmvp)"/PocketMVP

# Should show something like:
# -rwxr-xr-x ... Jul 31 11:24 .../PocketMVP
#                                  ^^^^^^^^
# This should match the time you ran xcodebuild.

`



If you want to verify the actual code that's running, swiftc -parse ios/PocketMVP/Views/SectionListView.swift should show "OK" (no errors) and the file should contain CollapsibleSection / arrow.up.arrow.down references.

Optional: clean UserDefaults first
If you want to start fresh (no collapsed-state from previous run), wipe the app's preferences:
`

# Wipe UserDefaults for the app (kill the state from prior runs)
xcrun simctl spawn booted defaults delete com.shoothigh.pocketmvp

`

This is the same as "factory reset" for the app's saved preferences. Only do this if you want to test the default state (all sections collapsed on first launch). Skip this if you want to verify the persistence works (collapse sections, kill the app, reopen, see them stay collapsed).

One-liner
`
xcrun simctl terminate booted com.shoothigh.pocketmvp && \
  xcrun simctl launch booted com.shoothigh.pocketmvp
`




That's the command. Run it once, then check the simulator — the tests course should show 3 sections collapsed (just titles with chevrons).






```