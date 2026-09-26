// The server supplies a base element for standalone and mounted pages.
export const APP_PREFIX = typeof document === 'undefined' ? '' : new URL(document.baseURI).pathname.replace(/\/$/, '');
export const appURL = path => APP_PREFIX + '/' + path.replace(/^\/+/, '');
