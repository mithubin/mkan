// Bridge: postMessage interface between mkan (parent) and Excalidraw (iframe)
// Injected via nginx sub_filter into cali.milan.how context
window.addEventListener('message', function(e) {
  if (!e.data || !e.source) return;
  var type = e.data.type;
  var src = e.source;
  var origin = e.origin || '*';

  if (type === 'MKAN_GET_SCENE') {
    try {
      var elements = JSON.parse(localStorage.getItem('excalidraw') || '[]');
      var appState = JSON.parse(localStorage.getItem('excalidraw-state') || '{}');
      src.postMessage({
        type: 'MKAN_SCENE_DATA',
        scene: {
          type: 'excalidraw', version: 2, source: location.origin,
          elements: Array.isArray(elements) ? elements : [],
          appState: {
            gridSize: appState.gridSize || null,
            viewBackgroundColor: appState.viewBackgroundColor || '#ffffff'
          },
          files: {}
        }
      }, origin);
    } catch (err) {
      src.postMessage({ type: 'MKAN_SCENE_ERROR', error: String(err) }, origin);
    }

  } else if (type === 'MKAN_LOAD_SCENE' && e.data.scene) {
    try {
      var scene = e.data.scene;
      localStorage.setItem('excalidraw', JSON.stringify(scene.elements || []));
      localStorage.setItem('excalidraw-state', JSON.stringify(scene.appState || {}));
      src.postMessage({ type: 'MKAN_LOAD_OK' }, origin);
      // Short delay so Excalidraw can pick up the new localStorage values
      setTimeout(function() { location.reload(); }, 100);
    } catch (err) {
      src.postMessage({ type: 'MKAN_LOAD_ERROR' }, origin);
    }
  }
});
