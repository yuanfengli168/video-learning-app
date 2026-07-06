cat << 'EOF' >> handover.md

## 11. Detailed Frontend Layout & UI Specifications

### Global Layout Structure
*   1 Sidebar + 1 Main Content Area.
*   **Desktop:** Fixed Sidebar (left, 250px). Main area takes remaining space.
*   **Mobile:** Sidebar hidden. Hamburger menu (☰) in top-left opens it as an overlay drawer.
*   **Top Header Bar:** Spans main area. Left: Hamburger/Logo. Center: Breadcrumbs. Right: User Profile/Logout.

### Sidebar Contents
*   Global Search Bar.
*   Navigation: `Dashboard` (Home), `My Courses` (Expandable list), `Chat History`.
*   Footer: User Avatar, Settings.

### Homepage (Dashboard) Layout
*   **Upload Zone:** Large drag-and-drop box at the top for video files.
*   **Continue Learning:** Horizontal carousel of in-progress videos (Thumbnail, Title, Progress Bar).
*   **Your Courses:** Responsive grid (3 cols desktop, 1 col mobile) of Course Cards. Clicking opens Course View.

### Course View Layout
*   Header: Course Title.
*   Main Area: List of `Sections` as collapsible accordions. Expanding a Section lists its `Videos`. Clicking a Video opens the Video Player View.

### Video Player View Layout (Core Learning Page)
*   **Desktop (Split):**
    *   Left (60%): Video Player + Interactive Transcript with search bar below it.
    *   Right (40%): Tabbed interface (`Summary`, `Flashcards`, `Quiz`, `Mindmap`, `Chat`).
*   **Mobile (Stacked):**
    *   Top: Video Player.
    *   Middle: Tabbed interface (Transcript moved into a tab to save space).
*   **Tabs Content:**
    *   *Flashcards:* Flippable cards. Includes "Teach me real-world usage" button.
    *   *Mindmap:* Interactive Markmap HTML.
    *   *Chat:* ChatGPT-style interface.

### Chat Interface Layout
*   Triggered by "Teach me real-world usage" button. Takes over right panel (Desktop) or full screen (Mobile).
*   Header: "Real-World Usage: [Concept]" + Back button.
*   Body: Alternating chat bubbles (User right, AI left).
*   Footer: Text input + Send button. History saved to DB.
EOF
git add handover.md
git commit -m "Add detailed frontend layout specifications to handover doc"
git push origin main
