import { useParams } from "react-router-dom";
import Workspace from "../components/workspace/Workspace";

/** The build: an always-editable 3D workspace (components/workspace). */
export default function BuildPage() {
  const { id = "" } = useParams();
  return <Workspace key={id} id={id} />;
}
