# Jyotiveda logo update

The custom mark combines a curved J, a gold sun, and two connection points. The continuous stroke represents coordinated electricity supply between homes. The colours match the existing forest-green interface.

## Update your running frontend

1. Unzip the updated frontend download.
2. Copy its `src` folder, `public` folder and `index.html` into your current `jyotiveda-frontend` folder, replacing the matching files.
3. Keep your existing `node_modules` folder and environment configuration.
4. Refresh http://localhost:3000. If the browser tab still shows the previous icon, use Command + Shift + R.

No dependency changes are needed for this update. The development server can remain running while you replace these files.

## Logo files

- `public/jyotiveda-mark.svg`: mint and gold symbol for dark backgrounds.
- `public/jyotiveda-mark-dark.svg`: forest green and gold symbol for light backgrounds.
- `public/jyotiveda-logo.svg`: full wordmark on a transparent background.
- `public/favicon.svg`: browser-tab icon with a dark rounded background.

The SVG paths are editable vector geometry. The wordmark uses live text with font fallbacks. The interface uses the new mark in its sidebar and footer; lightning icons elsewhere continue to indicate electrical actions.
