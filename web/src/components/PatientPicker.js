import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { usePatients } from "@/lib/queries";
export default function PatientPicker({ value, onChange }) {
    const p = usePatients();
    return (_jsxs(Select, { value: value, onValueChange: onChange, children: [_jsx(SelectTrigger, { className: "w-40", "aria-label": "Choose patient", "data-testid": "patient-picker", children: _jsx(SelectValue, { placeholder: "Patient" }) }), _jsx(SelectContent, { children: p.data?.items.map((x) => _jsxs(SelectItem, { value: x.patient_id, children: [x.patient_id, " \u00B7 ", x.age_band] }, x.patient_id)) })] }));
}
