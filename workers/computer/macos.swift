import AppKit
import ApplicationServices
import ScreenCaptureKit
import ImageIO
import UniformTypeIdentifiers

// Screenshots and the click coordinate system share this cap, so a guest whose display is
// set from the PJ definition (ticket 343) is captured 1:1 and clicks land where they look.
// It matches the width project.schema.json allows; Windows and Linux keep their 1024 cap.
let maxCapture=2560

@main struct DesktopNative {
 static func main() async {
  do {
   let data=FileHandle.standardInput.readDataToEndOfFile()
   guard let r=try JSONSerialization.jsonObject(with:data) as? [String:Any],let action=r["action"] as? String else {throw Failure("invalid request")}
   if action == "screenshot" {
    guard CGPreflightScreenCaptureAccess() else { _=CGRequestScreenCaptureAccess();throw Failure("Screen Recording permission required for desktop-native") }
    let content=try await SCShareableContent.excludingDesktopWindows(false,onScreenWindowsOnly:true)
    guard let display=content.displays.first(where:{$0.displayID == CGMainDisplayID()}) else {throw Failure("main display unavailable")}
    let config=SCStreamConfiguration();config.width=min(maxCapture,Int(CGDisplayPixelsWide(display.displayID)));config.height=Int(Double(CGDisplayPixelsHigh(display.displayID))*Double(config.width)/Double(CGDisplayPixelsWide(display.displayID)));config.showsCursor=true
    let filter=SCContentFilter(display:display,excludingWindows:[])
    let img=try await SCScreenshotManager.captureImage(contentFilter:filter,configuration:config)
    let bytes=NSMutableData();guard let dest=CGImageDestinationCreateWithData(bytes,UTType.png.identifier as CFString,1,nil) else {throw Failure("PNG encoding failed")}
    CGImageDestinationAddImage(dest,img,nil);guard CGImageDestinationFinalize(dest) else {throw Failure("PNG encoding failed")}
    output(["ok":true,"image":bytes.base64EncodedString(),"mimeType":"image/png","width":img.width,"height":img.height]);return
   }
   guard AXIsProcessTrusted() else {
    let options=[kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String:true] as CFDictionary
    _=AXIsProcessTrustedWithOptions(options);throw Failure("Accessibility permission required for desktop-native")
   }
   switch action {
   case "move","click":
    let display=CGMainDisplayID(),bounds=CGDisplayBounds(display),width=min(maxCapture,Int(CGDisplayPixelsWide(display))),height=Int(Double(CGDisplayPixelsHigh(display))*Double(width)/Double(CGDisplayPixelsWide(display)))
    guard let x=r["x"] as? Int,let y=r["y"] as? Int,x>=0,y>=0,x<width,y<height else {throw Failure("coordinates outside screenshot")}
    let point=CGPoint(x:bounds.origin.x+Double(x)*bounds.width/Double(width),y:bounds.origin.y+Double(y)*bounds.height/Double(height))
    CGEvent(mouseEventSource:nil,mouseType:.mouseMoved,mouseCursorPosition:point,mouseButton:.left)?.post(tap:.cghidEventTap)
    if action == "click" {
     let right=(r["button"] as? String)=="right",count=(r["count"] as? Int)==2 ? 2:1
     for n in 1...count {
      for type in [right ? CGEventType.rightMouseDown:.leftMouseDown,right ? .rightMouseUp:.leftMouseUp] {
       let e=CGEvent(mouseEventSource:nil,mouseType:type,mouseCursorPosition:point,mouseButton:right ? .right:.left);e?.setIntegerValueField(.mouseEventClickState,value:Int64(n));e?.post(tap:.cghidEventTap)
      }
      try await Task.sleep(nanoseconds:60_000_000)
     }
    }
   case "type":
    // Paste a single Unicode string. Bursts of synthetic key events can lose
    // characters in AppKit text views, especially newlines and long input.
    let text=r["text"] as? String ?? ""
    guard text.utf8.count<=8192 else {throw Failure("text exceeds 8192 bytes")}
    if !text.isEmpty {
     let clipboard=NSPasteboard.general;clipboard.clearContents()
     guard clipboard.setString(text,forType:.string) else {throw Failure("clipboard write failed")}
     let down=CGEvent(keyboardEventSource:nil,virtualKey:9,keyDown:true)
     down?.flags = .maskCommand;down?.post(tap:.cghidEventTap)
     let up=CGEvent(keyboardEventSource:nil,virtualKey:9,keyDown:false)
     up?.flags = .maskCommand;up?.post(tap:.cghidEventTap)
     try await Task.sleep(nanoseconds:200_000_000)
    }
   case "key":
    // Key names are the 3 OS contract (ticket 344); values are kVK_ANSI_* / kVK_* virtual key
    // codes for the US layout. "WIN" is an alias of Command, and "+" is Equal (24) plus SHIFT.
    let codes:[String:CGKeyCode]=["A":0,"S":1,"D":2,"F":3,"H":4,"G":5,"Z":6,"X":7,"C":8,"V":9,"B":11,"Q":12,"W":13,"E":14,"R":15,"Y":16,"T":17,"1":18,"2":19,"3":20,"4":21,"6":22,"5":23,"9":25,"7":26,"8":28,"0":29,"O":31,"U":32,"I":34,"P":35,"ENTER":36,"L":37,"J":38,"K":40,"N":45,"M":46,"TAB":48,"SPACE":49,"BACKSPACE":51,"ESC":53,"CMD":55,"SHIFT":56,"ALT":58,"CTRL":59,"LEFT":123,"RIGHT":124,"DOWN":125,"UP":126,"DELETE":117,"HOME":115,"END":119,"PAGEUP":116,"PAGEDOWN":121,"F1":122,"F2":120,"F3":99,"F4":118,"F5":96,"F6":97,"F7":98,"F8":100,"F9":101,"F10":109,"F11":103,"F12":111,"=":24,"-":27,"]":30,"[":33,"'":39,";":41,"\\":42,",":43,"/":44,".":47,"`":50,"+":24,"WIN":55]
    let keys=(r["keys"] as? [String] ?? []).map{$0.uppercased()}
    guard !keys.isEmpty,keys.count<=4 else {throw Failure("provide 1 to 4 keys")}
    for key in keys where codes[key] == nil {throw Failure("unsupported key: \(key.prefix(12)); supported: \(codes.keys.sorted().joined(separator:" "))")}
    var flags=CGEventFlags();for key in keys {switch key {case "CMD","WIN":flags.insert(.maskCommand);case "CTRL":flags.insert(.maskControl);case "ALT":flags.insert(.maskAlternate);case "SHIFT","+":flags.insert(.maskShift);default:break}}
    for key in keys {let e=CGEvent(keyboardEventSource:nil,virtualKey:codes[key]!,keyDown:true);e?.flags=flags;e?.post(tap:.cghidEventTap)}
    for key in keys.reversed() {let e=CGEvent(keyboardEventSource:nil,virtualKey:codes[key]!,keyDown:false);e?.post(tap:.cghidEventTap)}
   case "scroll":
    guard let amount=r["amount"] as? Int,amount != 0,abs(amount)<=20 else {throw Failure("invalid scroll amount")}
    CGEvent(scrollWheelEvent2Source:nil,units:.line,wheelCount:1,wheel1:Int32(amount),wheel2:0,wheel3:0)?.post(tap:.cghidEventTap)
   default:throw Failure("unsupported action")
   }
   output(["ok":true])
  } catch {output(["ok":false,"error":String(describing:error)]);exit(1)}
 }
 static func output(_ obj:[String:Any]) {if let data=try? JSONSerialization.data(withJSONObject:obj,options:[.sortedKeys]) {FileHandle.standardOutput.write(data);FileHandle.standardOutput.write(Data([10]))}}
 struct Failure:Error,CustomStringConvertible {let description:String;init(_ s:String){description=s}}
}
