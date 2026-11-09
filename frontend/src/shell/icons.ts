// Registry icon names (lucide ids from module-registry.json) -> lucide-react components (owner P08).
import {
  ArrowLeftRight, BarChart3, Briefcase, Building2, CheckCircle2, CircleAlert, FileText, Landmark, LayoutDashboard, Package, Rocket,
  Settings, ShoppingCart, Store, Users, Wallet, type LucideIcon,
} from "lucide-react";

const ICONS: Record<string, LucideIcon> = {
  "layout-dashboard": LayoutDashboard,
  landmark: Landmark,
  wallet: Wallet,
  "building-2": Building2,
  users: Users,
  "file-text": FileText,
  store: Store,
  package: Package,
  "shopping-cart": ShoppingCart,
  briefcase: Briefcase,
  "bar-chart-3": BarChart3,
  "check-circle-2": CheckCircle2,
  "arrow-left-right": ArrowLeftRight,
  rocket: Rocket,
  settings: Settings,
};

export function iconFor(name: string | undefined): LucideIcon {
  return (name && ICONS[name]) || CircleAlert;
}
