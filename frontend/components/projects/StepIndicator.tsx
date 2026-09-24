import "@/styles/components/step-indicator.css";

export function StepIndicator({ steps, currentIndex }: { steps: string[]; currentIndex: number }) {
  return (
    <div className="step-indicator">
      {steps.map((label, i) => (
        <div key={label} className="step-indicator__item">
          <div
            className={`step-indicator__circle${
              i < currentIndex ? " step-indicator__circle--done" : i === currentIndex ? " step-indicator__circle--active" : ""
            }`}
          >
            {i < currentIndex ? "\u2713" : i + 1}
          </div>
          <span className="step-indicator__label">{label}</span>
          {i < steps.length - 1 && <div className="step-indicator__line" />}
        </div>
      ))}
    </div>
  );
}
