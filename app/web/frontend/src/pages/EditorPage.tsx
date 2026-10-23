import { Link, useParams } from "react-router-dom";
import { editorUrl } from "../api";

// The 3D editor is its own page (viewer/editor.html) in an iframe, for the same
// reason the viewer is: its three.js/ldbi scripts are classic globals.
export default function EditorPage() {
  const { id = "" } = useParams();
  return (
    <div className="editor-page">
      <header className="chat-head">
        <Link to={`/chat/${id}`}>← Back to the chat</Link>
      </header>
      <iframe className="editor-frame" src={editorUrl(id)} title="3D model editor" />
    </div>
  );
}
