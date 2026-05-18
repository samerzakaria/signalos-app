import { h } from 'preact';

export function BrainView() {
  return (
    <>
<div className="view" data-view="brain">
        <div className="page-head">
          <h1>Brain</h1>
          <p>Notes, decisions, and artifacts saved across your project.</p>
        </div>
        <div className="stack">
          <div style={{ 'display': 'flex', 'alignItems': 'center', 'gap': '12px', 'marginBottom': '4px' }}>
            <div className="sb-search" style={{ 'flex': '1', 'margin': '0' }}>
              <i className="ti ti-search"></i>
              <input placeholder="Search notes and decisions…"/>
            </div>
            <button className="btn btn-soft" onClick={() => window.addBrainEntry()}><i className="ti ti-plus"></i> Add note</button>
          </div>
          <div className="brain-type-seg">
            <div className="brain-type active" onClick={() => window.filterBrain(this,'all')}>All</div>
            <div className="brain-type" onClick={() => window.filterBrain(this,'note')}><i className="ti ti-notes" style={{ 'fontSize': '13px' }}></i> Notes</div>
            <div className="brain-type" onClick={() => window.filterBrain(this,'decision')}><i className="ti ti-scale" style={{ 'fontSize': '13px' }}></i> Decisions</div>
            <div className="brain-type" onClick={() => window.filterBrain(this,'artifact')}><i className="ti ti-file-code" style={{ 'fontSize': '13px' }}></i> Artifacts</div>
            <div className="brain-type" onClick={() => window.filterBrain(this,'qa')}><i className="ti ti-help-circle" style={{ 'fontSize': '13px' }}></i> Q&amp;A</div>
          </div>
          <div className="card">
            <div className="brain-row">
              <div className="brain-type-ic decision"><i className="ti ti-scale"></i></div>
              <div className="brain-tx">
                <div className="brain-title">Pineapple pizza worth same points as others</div>
                <div className="brain-body">Decided to keep scoring uniform — all pizzas worth 10 points regardless of toppings. Makes the game fair and easier to understand.</div>
                <div className="brain-meta">
                  <span>today · Gate 4</span>
                  <span className="brain-tag">Decision</span>
                  <span className="brain-tag">Scoring</span>
                </div>
              </div>
            </div>
            <div className="brain-row">
              <div className="brain-type-ic note"><i className="ti ti-notes"></i></div>
              <div className="brain-tx">
                <div className="brain-title">Toppings list confirmed by Samer</div>
                <div className="brain-body">Cheese, pepperoni, mushroom, green pepper, pineapple, olives. Six total. Kids can mix and match.</div>
                <div className="brain-meta">
                  <span>today · Gate 4</span>
                  <span className="brain-tag">Note</span>
                  <span className="brain-tag">Toppings</span>
                </div>
              </div>
            </div>
            <div className="brain-row">
              <div className="brain-type-ic artifact"><i className="ti ti-file-code"></i></div>
              <div className="brain-tx">
                <div className="brain-title">recipes.js — three pizza recipes</div>
                <div className="brain-body">Margherita, Pepperoni Feast, Veggie Delight. Each has a required toppings array and a point value.</div>
                <div className="brain-meta">
                  <span>today · Gate 4</span>
                  <span className="brain-tag">Artifact</span>
                  <span className="brain-tag">Code</span>
                </div>
              </div>
            </div>
            <div className="brain-row">
              <div className="brain-type-ic qa"><i className="ti ti-help-circle"></i></div>
              <div className="brain-tx">
                <div className="brain-title">Q: Should the menu scroll or fit on screen?</div>
                <div className="brain-body">A: Fit on screen for now — only 3 pizzas, so a grid of 3 cards works. Revisit if we add more in gate 5.</div>
                <div className="brain-meta">
                  <span>yesterday · Gate 3</span>
                  <span className="brain-tag">Q&amp;A</span>
                  <span className="brain-tag">Layout</span>
                </div>
              </div>
            </div>
            <div className="brain-row">
              <div className="brain-type-ic decision"><i className="ti ti-scale"></i></div>
              <div className="brain-tx">
                <div className="brain-title">Stack: React + Vite, not plain HTML</div>
                <div className="brain-body">Chose React/Vite because the drag-and-drop in gate 5 will need component state. Plain HTML would require a rewrite later.</div>
                <div className="brain-meta">
                  <span>yesterday · Gate 2</span>
                  <span className="brain-tag">Decision</span>
                  <span className="brain-tag">Architecture</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
