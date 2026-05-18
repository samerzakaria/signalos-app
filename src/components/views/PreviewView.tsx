import { h } from 'preact';

export function PreviewView() {
  return (
    <>
<div className="view" data-view="preview">
        <div className="page-head">
          <h1>Live preview</h1>
          <p>See your app the way other people will. It updates as we build.</p>
        </div>
        <div className="stack">
          <div className="pv-bar">
            <div className="dev-seg">
              <div className="dev-b" data-device="mobile" onClick={() => window.switchDevice('mobile')}><i className="ti ti-device-mobile"></i> Phone</div>
              <div className="dev-b" data-device="tablet" onClick={() => window.switchDevice('tablet')}><i className="ti ti-device-tablet"></i> Tablet</div>
              <div className="dev-b active" data-device="desktop" onClick={() => window.switchDevice('desktop')}><i className="ti ti-device-desktop"></i> Big screen</div>
            </div>
            <div className="pv-url"><i className="ti ti-lock"></i> localhost:3000 / my-pizza-game</div>
            <div className="ico" onClick={() => window.refreshPreview()} aria-label="Refresh"><i className="ti ti-refresh"></i></div>
            <div className="ico" onClick={() => window.openExternal()} aria-label="Open externally"><i className="ti ti-external-link"></i></div>
          </div>
          <div className="pv-stage">
            <div className="pv-device desktop" id="pvDevice">
              <div className="game">
                <div className="g-head">
                  <div className="g-title">🍕 Pizza Time</div>
                  <div className="g-score"><i className="ti ti-trophy" style={{ 'fontSize': '13px', 'color': '#8A4A12' }}></i> 0 points</div>
                </div>
                <div className="g-board">
                  <div style={{ 'fontSize': '13px', 'color': '#8A4A12', 'fontWeight': '600' }}>Make a pizza for the next order</div>
                  <div className="g-order">🧀 Cheese · 🌶️ Pepper · 🍄 Mushroom</div>
                  <div className="pizzas">
                    <div className="pz"><div className="tp"></div></div>
                    <div className="pz"><div className="tp"></div></div>
                    <div className="pz"><div className="tp"></div></div>
                  </div>
                  <div className="g-ctrl">
                    <div className="g-btn">🍅 Tomato</div>
                    <div className="g-btn">🧀 Cheese</div>
                    <div className="g-btn">✨ Deliver</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
