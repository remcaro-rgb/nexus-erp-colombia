/** Typed client of the employee portal API (`/api/portal/v1/employee/*`); the party scope comes from the token only. */
import type { ApiClient } from "@lib/api/client";
import type {
  EmployeeCertificate,
  EmployeeDataUpdate,
  EmployeePayslip,
  EmployeeProfile,
  EmployeeVacation,
  PortalKpi,
} from "./model";

const BASE = "/api/portal/v1/employee";

interface ItemsPage<T> {
  items: T[];
  next_cursor: string | null;
}

export function employeePortalApi(api: ApiClient) {
  return {
    payslips: (signal?: AbortSignal) => api.get<ItemsPage<EmployeePayslip>>(`${BASE}/payslips`, { signal }),
    certificates: (signal?: AbortSignal) => api.get<ItemsPage<EmployeeCertificate>>(`${BASE}/certificates`, { signal }),
    vacations: (signal?: AbortSignal) => api.get<ItemsPage<EmployeeVacation>>(`${BASE}/vacations`, { signal }),
    requestVacation: (start_date: string, end_date: string) =>
      api.post<EmployeeVacation>(`${BASE}/vacations`, { body: { start_date, end_date } }),
    dataUpdates: (signal?: AbortSignal) => api.get<ItemsPage<EmployeeDataUpdate>>(`${BASE}/data-updates`, { signal }),
    requestDataUpdate: (changes: Record<string, string>) =>
      api.post<EmployeeDataUpdate>(`${BASE}/data-updates`, { body: { changes } }),
    profile: (signal?: AbortSignal) => api.get<EmployeeProfile>(`${BASE}/profile`, { signal }),
    kpis: (signal?: AbortSignal) => api.get<{ kpis: PortalKpi[] }>(`${BASE}/kpis`, { signal }),
    payslipPdfUrl: (id: string) => `${BASE}/payslips/${encodeURIComponent(id)}/pdf`,
    certificatePdfUrl: (id: string) => `${BASE}/certificates/${encodeURIComponent(id)}/pdf`,
  };
}
