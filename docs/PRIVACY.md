# Privacy: what to declare

The public policy is `server/public/privacy/index.html`, served at https://psst.zigao.wang/privacy/ and
linked from the app's About screen. This file maps it to App Store Connect's privacy questions. Update both
whenever the app starts sending anything new.

## App Privacy ("nutrition label")

| Data type (App Store Connect) | Collected? | Linked to the user? | Used for tracking? | Purpose |
| --- | --- | --- | --- | --- |
| Location > Coarse Location | Yes: the center of a map area with no stories, rounded to 0.1° (about 10 km), only if "Help choose new areas" is on. The server stores only a daily count per H3 resolution 5 cell (about 250 km²). | No | No | Analytics (deciding where to research next) |
| User Content > Other User Content | Yes: the optional message in a problem report | No | No | App Functionality (fixing content) |
| Usage Data > Other Usage Data | Yes: which story a problem report is about, and the reason | No | No | App Functionality |
| Diagnostics, Identifiers, Contact Info, and everything else | No | | | |

Precise location is used only on the device and never leaves it, so it isn't "collected" in Apple's sense.

## Privacy manifest

The app ships `PrivacyInfo.xcprivacy`. It declares:

- no tracking and no tracking domains;
- the collected data types above;
- the required-reason API it uses: UserDefaults (reason CA92.1, the app's own settings).

## Server side

- nginx access logs for `psst.zigao.wang` keep IP addresses; Ubuntu's logrotate keeps them 14 days.
- The API stores no IP addresses: reports and daily per-cell demand counts only (tables `reports` and `demand`).
