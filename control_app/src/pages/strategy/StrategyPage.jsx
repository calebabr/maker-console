import { Navigate, useParams } from 'react-router-dom';
import SubNav from '../../components/SubNav.jsx';
import { STRATEGY_PAGES, STRATEGY_VIEWS, strategyPath } from '../../utils/routes.js';
import StrategyOverview from './StrategyOverview.jsx';
import StrategyResults from './StrategyResults.jsx';
import StrategyControls from './StrategyControls.jsx';

const VIEWS = { overview: StrategyOverview, results: StrategyResults, controls: StrategyControls };

/**
 * Strategy page shell: /strategies/:id, /strategies/:id/results, /strategies/:id/controls.
 * Renders the one <h1> (the strategy's short name, which is also the page title), the sub-nav
 * (Overview | Results | Controls) and the current view. An unknown id redirects to /strategies.
 * Wave 2 (FE-Strategy-Pages) fills the three view files in this folder; the shell stays as is.
 *   <Route path="/strategies/:id/results" element={<StrategyPage view="results" />} />
 */
export default function StrategyPage({ view = 'overview' }) {
  const { id } = useParams();
  if (!Object.prototype.hasOwnProperty.call(STRATEGY_PAGES, id)) return <Navigate to="/strategies" replace />;
  const View = VIEWS[view] || StrategyOverview;
  const items = STRATEGY_VIEWS.map((v) => ({ to: strategyPath(id, v.id), label: v.label }));
  return (
    <div className="page">
      <div className="page-head">
        <h1>{STRATEGY_PAGES[id]}</h1>
      </div>
      <SubNav items={items} label={`${STRATEGY_PAGES[id]} views`} />
      <View id={id} />
    </div>
  );
}
