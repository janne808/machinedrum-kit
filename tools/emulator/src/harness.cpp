#include "mdLib/mdhardware.h"
#include "mdLib/mdromloader.h"
#include "baseLib/filesystem.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <vector>

namespace fs = std::filesystem;
constexpr unsigned rate = md::g_samplerate;
void require(bool ok, const char* why) { if(!ok) throw std::runtime_error(why); }
void le(std::ostream& f, uint32_t v, unsigned n) { for(unsigned i=0;i<n;++i) f.put(char(v>>(8*i))); }
void wav(const fs::path& path, const std::vector<float>& samples) {
 std::ofstream f(path, std::ios::binary); require(bool(f),"Cannot create WAV");
 f.write("RIFF",4); le(f,36+samples.size()*2,4); f.write("WAVEfmt ",8);
 le(f,16,4);le(f,1,2);le(f,2,2);le(f,rate,4);le(f,rate*4,4);le(f,4,2);le(f,16,2);
 f.write("data",4);le(f,samples.size()*2,4);
 for(float v:samples) le(f,static_cast<uint16_t>(static_cast<int16_t>(std::clamp(v,-1.f,1.f)*32767)),2);
 require(bool(f),"WAV write failed");
}
void lcd(const fs::path& path, const md::FrontPanel& panel) {
 std::ofstream f(path);f<<"P1\n128 64\n";
 for(unsigned y=0;y<64;++y) {for(unsigned x=0;x<128;++x) f<<panel.getLcdPixel(x,y)<<' ';f<<'\n';}
 require(bool(f),"LCD write failed");
}
bool sameLcd(const md::FrontPanel& a,const md::FrontPanel& b) {
 for(unsigned y=0;y<64;++y) for(unsigned x=0;x<128;++x) if(a.getLcdPixel(x,y)!=b.getLcdPixel(x,y)) return false;
 return true;
}
// GPL-3.0. md-harness: boot the firmware from reset and check MIDI, panel and audio.
int main(int argc, char** argv) {
 const bool experimental=argc==6 && std::string(argv[5])=="--experimental-firmware";
 if(argc!=5 && !experimental) {std::cerr<<"Usage: md-harness FIRMWARE OUTPUT_DIR EXPECTED_MAIN_OS BOOT_FRAME_LIMIT [--experimental-firmware]\n";return 2;}
 fs::path out=argv[2];
 std::unique_ptr<md::Hardware> hw;
 uint64_t frames=0,bootFrames=0,nonzero=0;
 bool boot=false,ram=false,sysex=false,panel=false,audio=false;
 float peak=0,quietPeak=0;double rms=0;
 unsigned statusValue=0;
 std::string failure;
 try {
  fs::create_directories(out);
  size_t consumed=0;const auto limit=std::stoull(argv[4],&consumed);
  require(consumed==std::string(argv[4]).size() && limit>0 && limit<=rate*120,"Invalid boot frame limit");
  std::vector<uint8_t> rom,expected;
  require(baseLib::filesystem::readFile(rom,argv[1]),"Cannot read firmware");
  // Prevent Hardware's fallback ROM search: accept the canonical image, or exactly this
  // image when explicitly opted in (the caller validates its structure first).
  if(experimental) md::RomLoader::allowExperimentalImage(rom);
  require(md::RomLoader::isRomForModel(rom,md::MachineModel::Machinedrum),"Unsupported firmware fingerprint");
  require(baseLib::filesystem::readFile(expected,argv[3]) && expected.size()>=256,"Missing independent main OS extraction");
  hw=std::make_unique<md::Hardware>(rom,argv[1]);
  require(hw->isValid() && hw->getUC().getPC()==12,"Reset vector mismatch");
  std::ofstream trace(out/"trace.jsonl");
  auto record=[&](const char* phase) {
   trace<<"{\"phase\":\""<<phase<<"\",\"frames\":"<<frames
    <<",\"cpu_pc\":"<<hw->getUC().getPC()<<",\"cpu_cycles\":"<<hw->getUC().getCycles()
    <<",\"dsp1_pc\":"<<hw->getDspMixer().dsp().getPC().toWord()<<",\"dsp2_pc\":"<<hw->getDspProducer().dsp().getPC().toWord()
    <<",\"dsp1_cycles\":"<<hw->getDspMixer().dsp().getCycles()<<",\"dsp2_cycles\":"<<hw->getDspProducer().dsp().getCycles()
    <<",\"dsp_ready\":"<<hw->isAudioReady()<<",\"midi_ready\":"<<hw->isFirmwareMidiReady()
    <<",\"lcd_pixels\":"<<hw->getFrontPanelSnapshot().countLitPixels()<<"}\n";trace.flush();
  };
  record("reset");
  while(frames<limit) {
   const auto n=static_cast<unsigned>(std::min<uint64_t>(256,limit-frames));hw->advance(n);frames+=n;
   if(frames%4096==0) record("boot");
   if(hw->isFirmwareMidiReady() && hw->getFrontPanelSnapshot().countLitPixels()>0) {boot=true;break;}
  }
  bootFrames=frames;record("boot_boundary");require(boot,"Boot deadline exceeded");
  // Code prefix is immutable; later OS data/BSS bytes are expected to change during boot.
  ram=true;for(unsigned i=0;i<256;++i) ram &= hw->getUC().read8(0x200000+i)==expected[i];
  require(ram,"Guest-decompressed main OS prefix differs from Python extraction");
  auto advance=[&](unsigned n) {while(n) {const auto k=std::min(n,256u);hw->advance(k);frames+=k;n-=k;}};
  advance(rate*3); // let application tasks finish initialization
  record("settled");lcd(out/"boot.pbm",hw->getFrontPanelSnapshot());
  std::vector<synthLib::SMidiEvent> replies;hw->readMidiOut(replies);replies.clear();
  synthLib::SMidiEvent request(synthLib::MidiEventSource::Host);
  request.sysex={0xf0,0x00,0x20,0x3c,0x02,0x00,0x70,0x02,0xf7}; // current kit
  require(hw->sendMidi(request),"MIDI request rejected");
  std::ofstream midi(out/"midi-replies.hex");
  for(unsigned attempt=0;attempt<80 && !sysex;++attempt) {
   advance(2048);replies.clear();hw->readMidiOut(replies);
   for(const auto& e:replies) {
    for(auto b:e.sysex) midi<<std::hex<<std::setw(2)<<std::setfill('0')<<unsigned(b)<<' ';midi<<'\n';
    const auto& s=e.sysex;
    if(s.size()==10 && std::equal(request.sysex.begin(),request.sysex.begin()+6,s.begin()) && s[6]==0x72 && s[7]==2 && s[8]<64 && s[9]==0xf7) {sysex=true;statusValue=s[8];}
   }
  }
  require(sysex,"Firmware did not reply to current-kit SysEx request");record("sysex_reply");
  const auto before=hw->getFrontPanelSnapshot();
  auto tap=[&](md::PanelControl control) {
   const auto p=md::panelPacket(md::MachineModel::Machinedrum,control);require(p.has_value(),"Unknown panel control");
   require(hw->trySendPanelEvent(p->row,p->mask),"Panel press rejected");advance(2048);
   require(hw->trySendPanelEvent(p->row,0),"Panel release rejected");advance(rate/2);
  };
  tap(md::PanelControl::Tempo);const auto tempo=hw->getFrontPanelSnapshot();lcd(out/"tempo.pbm",tempo);
  tap(md::PanelControl::Exit);panel=!sameLcd(before,tempo) && !sameLcd(tempo,hw->getFrontPanelSnapshot());
  require(panel,"Panel menu entry/exit did not change LCD");record("panel_response");
  std::array<std::array<float,256>,6> channels{};synthLib::TAudioOutputs outputs{};
  for(unsigned c=0;c<6;++c) outputs[c]=channels[c].data();
  hw->processAudio(outputs,0,1);hw->processAudio(outputs,0,0); // re-prime after headless advance
  hw->resetHostAudioInputQueueTelemetry();
  auto render=[&](unsigned n,std::vector<float>* capture,float& max) {
   while(n) {
    const auto k=std::min(n,256u);hw->processAudio(outputs,k,0);frames+=k;n-=k;
    for(unsigned i=0;i<k;++i) {
     for(unsigned c=0;c<6;++c) require(std::isfinite(channels[c][i]),"Non-finite DSP output");
     for(unsigned c=0;c<2;++c) {float v=channels[c][i];max=std::max(max,std::abs(v));if(capture) capture->push_back(v);}
    }
   }
  };
  render(rate,nullptr,quietPeak);
  std::vector<float> capture;capture.reserve(rate*8);
  // Factory mapping: host note 36 triggers track 1. Send through UART1, no DSP/RAM injection.
  for(unsigned hit=0;hit<4;++hit) {
   require(hw->sendMidi({synthLib::MidiEventSource::Host,0x90,36,static_cast<uint8_t>(100+hit*5)}),"Note-on rejected");
   render(rate/4,&capture,peak);
   require(hw->sendMidi({synthLib::MidiEventSource::Host,0x80,36,0}),"Note-off rejected");
   render(rate-rate/4,&capture,peak);
  }
  double energy=0;for(float v:capture) {energy+=double(v)*v;if(std::abs(v)>0.00001f) ++nonzero;}
  rms=std::sqrt(energy/capture.size());wav(out/"drums.wav",capture);
  audio=peak>0.001f && peak>quietPeak+0.001f && nonzero>100;
  record("audio_response");lcd(out/"final.pbm",hw->getFrontPanelSnapshot());
  require(audio,"No drum audio above idle baseline");
  require(hw->midiRxOverflowCount()==0 && hw->queuedMidiRxBytes()==0,"MIDI did not drain cleanly");
  require(hw->hostAudioInputUnderflowCount()==0 && hw->hostAudioInputOverflowCount()==0,"Audio input timeline over/underflow");
 } catch(const std::exception& e) { failure=e.what();std::cerr<<failure<<'\n'; }
 try {
  fs::create_directories(out);
  std::ofstream report(out/"runtime.json");report<<std::boolalpha
   <<"{\n  \"passed\": "<<failure.empty()<<",\n  \"boot_ready\": "<<boot<<",\n  \"boot_frames\": "<<bootFrames
   <<",\n  \"os_prefix_matches\": "<<ram<<",\n  \"sysex_reply\": "<<sysex<<",\n  \"current_kit\": "<<statusValue
   <<",\n  \"panel_response\": "<<panel<<",\n  \"audio_response\": "<<audio<<",\n  \"audio_peak\": "<<peak
   <<",\n  \"idle_peak\": "<<quietPeak<<",\n  \"audio_rms\": "<<rms<<",\n  \"nonzero_samples\": "<<nonzero
   <<",\n  \"frames\": "<<frames<<",\n  \"cpu_pc\": "<<(hw?hw->getUC().getPC():0)
   <<",\n  \"experimental_firmware\": "<<experimental<<",\n  \"task_list_workaround_enabled\": true,\n  \"hardware_accuracy_verified\": false\n}\n";
  require(bool(report),"Cannot write runtime report");
  if(!failure.empty()) {std::ofstream(out/"failure.txt")<<failure<<'\n';if(hw) lcd(out/"failure.pbm",hw->getFrontPanelSnapshot());}
 } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
 if(failure.empty()) std::cout<<"PASS: reset boot, guest decompression, DSPs, SysEx, LCD and MIDI-triggered audio\n";
 return failure.empty()?0:1;
}
