import { raisedBudget } from "./ChatSettings";

/** The short fixed message a "Continue" button sends: the work resumes from the saved files and chat. */
export const CONTINUE_TEXT = "Continue the work from where it stopped.";

/** Shown above the composer when a request stopped at its budget: continue the same work without typing. */
export default function BudgetStop({ budget, onContinue }: { budget: number; onContinue: (budget: number) => void }) {
  const raised = raisedBudget(budget);
  return <div className="budget-stop" role="alert">
    <p><strong>Stopped: budget reached</strong>
      This request used its €{+budget.toFixed(2)} budget. Everything done so far is kept. Continue to carry on from here.</p>
    <div className="actions">
      <button type="button" className="primary" onClick={() => onContinue(budget)}>Continue with +€{+budget.toFixed(2)}</button>
      <button type="button" onClick={() => onContinue(raised)}>Raise budget to €{+raised.toFixed(2)}</button>
    </div>
  </div>;
}
