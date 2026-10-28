import { Link } from "react-router-dom"

/** The reference list shown on the Report page. Numbers are stable: pages cite them with <Cite n={..} />. */
export const REFS: { n: number; text: string; url?: string; used: string }[] = [
  { n: 1, text: "Palermo F., Li Y., Capstick A., et al. TIHM: An open dataset for remote healthcare monitoring in dementia. Scientific Data 10, 606 (2023).",
    url: "https://doi.org/10.1038/s41597-023-02519-y", used: "The dataset: 56 people living with dementia, April–June 2019; labels are alerts confirmed by the study's monitoring team." },
  { n: 2, text: "Royal College of Physicians. National Early Warning Score (NEWS) 2: Standardising the assessment of acute-illness severity in the NHS. London: RCP (2017).",
    url: "https://www.rcp.ac.uk/improving-care/resources/national-early-warning-score-news-2/", used: "Red-flag limits for urgent alerts (systolic ≤ 90, pulse ≤ 40 or ≥ 131); indexed by the Evidence agent." },
  { n: 3, text: "National Institute for Health and Care Excellence. Hypertension in adults: diagnosis and management (NICE guideline NG136) (2019).",
    url: "https://www.nice.org.uk/guidance/ng136", used: "Severe hypertension (BP ≥ 180/120) as an urgent alert; indexed by the Evidence agent." },
  { n: 4, text: "Gelman A., Carlin J. B., Stern H. S., Dunson D. B., Vehtari A., Rubin D. B. Bayesian Data Analysis, 3rd ed. CRC Press (2013).",
    used: "Each person's usual (Normal-Inverse-Gamma) and personal event rate (Beta-Bernoulli) with priors learned from training patients." },
  { n: 5, text: "National Institute for Health and Care Excellence. Dementia: assessment, management and support for people living with dementia and their carers (NICE guideline NG97) (2018).",
    url: "https://www.nice.org.uk/guidance/ng97", used: "Guidance cited by the Evidence agent for agitation and distress." },
  { n: 6, text: "National Institute for Health and Care Excellence. Delirium: prevention, diagnosis and management (NICE clinical guideline CG103) (2010, updated 2023).",
    url: "https://www.nice.org.uk/guidance/cg103", used: "Indexed by the Evidence agent (sudden change in behaviour)." },
  { n: 7, text: "Chen T., Guestrin C. XGBoost: A scalable tree boosting system. Proc. 22nd ACM SIGKDD, 785–794 (2016).",
    url: "https://doi.org/10.1145/2939672.2939785", used: "Agitation detector classifier." },
  { n: 8, text: "Platt J. Probabilistic outputs for support vector machines and comparisons to regularized likelihood methods. In Advances in Large Margin Classifiers, MIT Press (1999).",
    used: "Calibration of the agitation probability." },
  { n: 9, text: "Salton G., Buckley C. Term-weighting approaches in automatic text retrieval. Information Processing & Management 24(5), 513–523 (1988).",
    url: "https://doi.org/10.1016/0306-4573(88)90021-0", used: "TF-IDF retrieval of guideline passages by the Evidence agent." },
  { n: 10, text: "Lewis P., Perez E., Piktus A., et al. Retrieval-augmented generation for knowledge-intensive NLP tasks. NeurIPS 33 (2020).",
    used: "Retrieve-then-write design of the Evidence agent: the text may only use the retrieved passages and must cite them." },
  { n: 11, text: "Wooldridge M., Jennings N. R. Intelligent agents: theory and practice. The Knowledge Engineering Review 10(2), 115–152 (1995).",
    url: "https://doi.org/10.1017/S0269888900008122", used: "Agent properties used here: autonomy, own state and goals, communication only by messages." },
  { n: 12, text: "Davis J., Goadrich M. The relationship between Precision-Recall and ROC curves. Proc. 23rd ICML, 233–240 (2006).",
    url: "https://doi.org/10.1145/1143844.1143874", used: "Why PR-AUC (not accuracy) is the main measure for rare events." },
  { n: 13, text: "Efron B., Tibshirani R. J. An Introduction to the Bootstrap. Chapman & Hall (1993).",
    used: "95% confidence intervals by resampling patients." },
]

/** In-text citation, e.g. <Cite n={1} /> or <Cite n={[4, 5]} />: links to the Report page's reference list. */
export function Cite({ n }: { n: number | number[] }) {
  const ns = Array.isArray(n) ? n : [n]
  return (
    <span className="whitespace-nowrap text-xs align-super">
      [{ns.map((k, i) => (
        <span key={k}>{i > 0 && ", "}<Link to={`/report#ref-${k}`} className="font-medium text-primary underline underline-offset-2" aria-label={`Reference ${k}`}>{k}</Link></span>
      ))}]
    </span>
  )
}
