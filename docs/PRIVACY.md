# Privacy: what to declare

The public policy is `server/public/privacy/index.html`, served at https://psst.zigao.wang/privacy/ and
linked from the app's Settings screen. This file maps it to App Store Connect's privacy questions. Update both
whenever the app starts sending anything new.

## App Privacy ("nutrition label")

| Data type (App Store Connect) | Collected? | Linked to the user? | Used for tracking? | Purpose |
| --- | --- | --- | --- | --- |
| Usage Data > Product Interaction | Yes: which empty area of the map someone looked at, as an H3 resolution 5 cell id (about 250 km²), only if "Help choose new areas" is on. Never sent while the person's own location is in view, so it's never their location. Each device sends a cell at most once a day and at most 10 a day. The server keeps only a daily count per cell. | No | No | Analytics (deciding where to research next) |
| User Content > Other User Content | Yes: the optional message in a problem report | No | No | App Functionality (fixing content) |
| Usage Data > Other Usage Data | Yes: which story a problem report is about, and the reason | No | No | App Functionality |
| Diagnostics, Identifiers, Contact Info, and everything else | No | | | |

Location, precise or coarse, is used only on the device and never leaves it, so it isn't "collected" in Apple's sense. Empty-area signals describe a part of the map, not the person, and are declared as product interaction.

## Privacy manifest

The app ships `PrivacyInfo.xcprivacy`. It declares:

- no tracking and no tracking domains;
- the collected data types above;
- the required-reason API it uses: UserDefaults (reason CA92.1, the app's own settings).

## Server side

- nginx logs for `psst.zigao.wang` use a format with no IP address, and the error log only records critical problems (ordinary request errors would include the address). Ubuntu's logrotate keeps them 14 days. Rate limits count requests per address in nginx's memory only.
- The API stores no IP addresses: reports and daily per-cell demand counts only (tables `reports` and `demand`).
