// Makes the site installable as an app. Service workers only run on https or on this computer
// (localhost); on a phone over plain Wi-Fi the app still installs to the home screen, just without offline copies.
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
