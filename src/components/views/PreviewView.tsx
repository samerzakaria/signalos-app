import { previewDevice, previewUrl, previewStatus, workspacePath } from '../../state';
import { viewClass } from '../viewShell';

export function PreviewView() {
  const device = previewDevice.value;
  const url = previewUrl.value;
  const status = previewStatus.value;
  const ws = workspacePath.value;
  const devCls = (d: string) => device === d ? 'dev-b active' : 'dev-b';

  const isRunning = status === 'running' && !!url;
  const isBusy = status === 'starting' || status === 'installing';
  const statusLabel = isBusy ? (status === 'installing' ? 'Installing…' : 'Starting…')
                    : status === 'error' ? 'Crashed'
                    : isRunning ? url
                    : ws ? 'No preview running'
                    : 'No workspace';

  return (
    <>
<div className={viewClass('preview')} data-view="preview">
        <div className="page-head">
          <h1>Live preview</h1>
          <p>See your app the way other people will. It updates as we build.</p>
        </div>
        <div className="stack">
          <div className="pv-bar">
            <div className="dev-seg">
              <div className={devCls('mobile')} data-device="mobile" onClick={() => window.switchDevice('mobile')}><i className="ti ti-device-mobile"></i> Phone</div>
              <div className={devCls('tablet')} data-device="tablet" onClick={() => window.switchDevice('tablet')}><i className="ti ti-device-tablet"></i> Tablet</div>
              <div className={devCls('desktop')} data-device="desktop" onClick={() => window.switchDevice('desktop')}><i className="ti ti-device-desktop"></i> Big screen</div>
            </div>
            <div className="pv-url">
              <i className={`ti ${isRunning ? 'ti-lock' : isBusy ? 'ti-loader-2' : 'ti-circle-off'}`} style={isBusy ? { animation: 'spin 1s linear infinite' } : undefined}></i> {statusLabel}
            </div>
            {isRunning ? (
              <>
                <div className="ico" onClick={() => window.previewReload()} aria-label="Reload" title="Reload"><i className="ti ti-refresh"></i></div>
                <div className="ico" onClick={() => window.previewStop()} aria-label="Stop" title="Stop"><i className="ti ti-player-stop"></i></div>
                <div className="ico" onClick={() => window.openExternal()} aria-label="Open externally" title="Open in browser"><i className="ti ti-external-link"></i></div>
              </>
            ) : (
              <div className="ico" onClick={() => { if (ws) window.previewRun(); }} aria-label="Run preview" title={ws ? 'Run dev server' : 'No workspace set'} style={!ws ? { opacity: 0.4, cursor: 'not-allowed' } : undefined}>
                <i className="ti ti-player-play"></i>
              </div>
            )}
          </div>
          <div className="pv-stage">
            <div className={`pv-device ${device}`} id="pvDevice">
              {isRunning ? (
                <iframe
                  src={url}
                  title="Live preview"
                  style={{ width: '100%', height: '100%', border: 0, background: '#fff' }}
                  sandbox="allow-scripts allow-forms allow-same-origin allow-popups"
                />
              ) : isBusy ? (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', flexDirection: 'column', gap: '12px', color: 'var(--ink-3)' }}>
                  <i className="ti ti-loader-2" style={{ fontSize: '32px', animation: 'spin 1s linear infinite' }}></i>
                  <p>{statusLabel}</p>
                </div>
              ) : status === 'error' ? (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', flexDirection: 'column', gap: '12px', color: 'var(--danger-deep)' }}>
                  <i className="ti ti-alert-circle" style={{ fontSize: '32px' }}></i>
                  <p>Dev server crashed. Check the Build conversation for logs.</p>
                </div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', flexDirection: 'column', gap: '12px', color: 'var(--ink-3)', textAlign: 'center', padding: '24px' }}>
                  <i className="ti ti-device-desktop-off" style={{ fontSize: '32px' }}></i>
                  <p style={{ maxWidth: '320px', fontSize: '13px' }}>
                    {ws
                      ? <>No dev server running. Press <i className="ti ti-player-play" style={{ verticalAlign: 'middle' }}></i> to install dependencies and start it.</>
                      : 'Set a workspace folder in Settings to enable preview.'}
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
