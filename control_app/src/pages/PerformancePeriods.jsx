import PeriodsCard from '../components/PeriodsCard.jsx';

/** Periods and cash flows (moved here from Account); the card is unchanged. */
export default function PerformancePeriods() {
  return (
    <div className="page">
      <div className="page-head">
        <h1>Periods</h1>
      </div>
      <PeriodsCard />
    </div>
  );
}
