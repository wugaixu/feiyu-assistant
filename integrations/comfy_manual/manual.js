// Keep old bookmarks and cached management-console links working.
import { app } from '/scripts/app.js';
app.registerExtension({
  name: 'Feiyu.ManualImages',
  setup() {
    if (new URL(location.href).searchParams.get('feiyu_manual') === '1') {
      location.replace('http://127.0.0.1:8189/');
    }
  }
});
