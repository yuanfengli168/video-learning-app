# MVP1.0 Post Release
- this one mainly focused on creating todos on the errors, I found on functions from MVP1.0, 
- but can also generate new idea for future, though this must be in mvp<future.versionNumber>.md as well



## MVP1.0 Functionalities: 



## Optimization: 
- before you start: for each topic, please do following, a) discuss and articulate your approach; b) typically use at least 2 commits for each, first is to implement the changes, second is to add tests and maintain 96% overall code base test coverage, but as high as possible coverage for this topic. c)if any tests failed, fix it; so a and b have to have, c depends on whether any tests need to be fixed, and if other thing also needed, can add d) e) etc.
- [optional]when you have a, b, c ... commits for same topic follow the pattern like: a) "Implementation of Transcript scrolling and highlighting functions with video - part A, implementation"; b) "Implementation of Transcript scrolling and highlighting functions with video - part B, tests"; c)"Implementation of Transcript scrolling and highlighting functions with video - part C, fix tests";

1. the transcript is not scrolling with the video, when video is at 1 minute, it still at 0 minute. 
   1. few options:
      1. like coursera move and scroll and move highlight for each entence
      2. highlight change from the transcript viewport on each sentence, but only scroll when reaching out the transcript viewport, (if not understand, can ask me)
2. when I generated materials and I logout and login again, and stay on this page: http://localhost:8000/video/e3025dcd-2342-46b3-98cf-d81352e195ee, the Summary still shows the Generate material button, but it actually needs to show the previous summary that already generated. 
3. so when I leave my laptop open for a really long time, I open I can't see my summary because I somehow logeout, BUT this Log out status is not shown, so might confuse others who not familiar with this, and if re login, then will see summary and video. 
4. I need a default for the characters for Chinese like Simplified chinese or Traditional Chinese, now I think if video had chinese in mandarine the transcript is showed in traditional chinese characters, but I want to have a default option for user to choose, (The UI can be done later for future MVP, but now I need something in Config files before I run the backend, etc.) Not familiar with this, so lets discuss.

## new idea: (No implementation on these, just discuss, and it should be updated in new markdown file for future mvp docs)
1. I would like the LLM to discuss transcript. and guide us to potential part of video: 
   1. for example I came back to video and there is a new tab after summary and before flashcards