import type { CapacitorConfig } from '@capacitor/cli';

const configuredUrl = process.env.MOBILE_APP_URL?.trim().replace(/\/$/, '');
const allowCleartext = process.env.CAPACITOR_ALLOW_CLEARTEXT === 'true';

let server: CapacitorConfig['server'];
if (configuredUrl) {
  const url = new URL(configuredUrl);
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && allowCleartext)) {
    throw new Error(
      'MOBILE_APP_URL must use HTTPS. For LAN-only development, set CAPACITOR_ALLOW_CLEARTEXT=true explicitly.',
    );
  }
  server = {
    url: configuredUrl,
    cleartext: url.protocol === 'http:',
    errorPath: 'index.html',
  };
}

const config: CapacitorConfig = {
  appId: 'ph.com.batangaspremium.meattrack',
  appName: 'MEATTRACK',
  webDir: 'mobile',
  loggingBehavior: 'debug',
  backgroundColor: '#fff8ec',
  appendUserAgent: ' MEATTRACK-Mobile/1.0',
  server,
  android: {
    allowMixedContent: false,
  },
};

export default config;
