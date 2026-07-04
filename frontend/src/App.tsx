import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import YieldMap from "./pages/YieldMap";
import Genealogy from "./pages/Genealogy";
import Classifier from "./pages/Classifier";
import KlarfUpload from "./pages/KlarfUpload";
import Products from "./pages/Products";
import Simulator from "./pages/Simulator";
import Analytics from "./pages/Analytics";
import Generate from "./pages/Generate";
import Lots from "./pages/Lots";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard"  element={<Dashboard />} />
          <Route path="/yield-map"  element={<YieldMap />} />
          <Route path="/genealogy"  element={<Genealogy />} />
          <Route path="/classifier" element={<Classifier />} />
          <Route path="/upload"     element={<KlarfUpload />} />
          <Route path="/products"   element={<Products />} />
          <Route path="/simulator"  element={<Simulator />} />
          <Route path="/analytics"  element={<Analytics />} />
          <Route path="/generate"   element={<Generate />} />
          <Route path="/lots"       element={<Lots />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
