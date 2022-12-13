# -*- coding: utf-8 -*-
"""
Standalone QT program to provide live preview of the idle mode measurements of an IFS125HR.

The program includes rudimentary FFT functionality and provides a smoothed (= low pass - filtered) interferogram 
to detect potential detector nonlinearity during alignment.

Author: Matthias Buschmann, IUP Bremen
Date: 2022/11/3
"""

from __future__ import print_function, division
import sys, yaml, requests, struct, io
from PyQt5.QtWidgets import QPushButton, QCheckBox
from matplotlib.backends.backend_qt5agg import FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import numpy as np

from matplotlib.backends.qt_compat import QtWidgets
import matplotlib.pyplot as plt


class ftsreader():
    ''' A striped down version of ftsreader, including support for using a data stream directly from IFS125, 
    without the need to save to disk first.
    
    Full version at: https://github.com/mbuschmann/ftsreader
    '''
    def search_header_par(self, par):
        '''search the header for parameter <par> and return datablock designation '''
        pars = []
        for i in list(self.header.keys()):
            for j in list(self.header[i].keys()):
                if par == j:
                    pars.append(i)
        if len(pars)==1:
            return pars[0]
        elif len(pars)>1:
            if self.verbose: print('Found parameter in multiple datablocks')
            return pars
        else:
            if self.verbose: print('Parameter', par, 'not found in header!')
            return None

    def get_header_par(self, par):
        try:
            return self.header[self.search_header_par(par)][par]
        except:
            print('Parameter not found in header ...')
            return None

    def read_structure(self):
        #t = time.time()
        '''Read the structure of the file and write to ftsreader.fs'''
        # known blocks so far, there is always a block zero, that is still unidentified
        self.__blocknames =    {'160': 'Sample Parameters',
                        '23': 'Data Parameters',
                        '96': 'Optic Parameters',
                        '64': 'FT Parameters',
                        '48': 'Acquisition Parameters',
                        '32': 'Instrument Parameters',
                        '7':  'Data Block',
                        '0':  'something'}
        self.__blocknames2 = {'132': ' ScSm', # another declaration to differentiate blocks between ifg, spc, etc.
                        '4': ' SpSm',
                        '8': ' IgSm',
                        '20': ' TrSm',
                        '12': ' PhSm',
                        b'\x84': ' SpSm/2.Chn.', # some weird stuff going on with python3 decoding here, use binary representation
                        b'\x88': ' IgSm/2.Chn.'}
        self.fs = {}
        fi = self.getfileobject()
        with fi as f: #open(self.path, 'rb') as f:
            f.seek(0)
            self.log.append('Reading structure of file')
            # read beginning of file to assert magic number, total number of blocks and first offset
            # some unidentified numbers in between, do not seem to be necessary for header, spc or ifg blocks
            (magic, something, something, offset1, something, numberofblocks) = struct.unpack('6i', f.read(struct.calcsize('6i')))
            f.seek(offset1) # start at first offset
            for i in range(numberofblocks): # go through all blocks and save all found blocks in self.fs
                s = f.read(struct.calcsize('2BH2i'))
                #read beginning of block, with infos on block types, something yet unidentified/unimportant of size 'H' for now, length and gobal offset of the block
                (blocktype, blocktype2, something, length, offset2) = struct.unpack('2BH2i',s)
                blocktype = str(blocktype)
                blocktype2 = str(blocktype2)
                if blocktype in self.__blocknames.keys():
                    hdrblockname = self.__blocknames[blocktype]
                else:
                    hdrblockname = '[unknown block '+blocktype+']'
                if blocktype2 in self.__blocknames2.keys():
                    hdrblockname += self.__blocknames2[blocktype2]
                else: pass
                self.log.append('Found block '+str(blocktype)+', '+str(blocktype2)+' and identified as '+hdrblockname)
                if blocktype == '0' or blocktype not in self.__blocknames.keys():
                    hdrblockname += ' len %3i' % (length)
                else:
                    pass
                self.fs[hdrblockname] = {'blocktype': blocktype, 'blocktype2': blocktype2, 'length': length, 'offset': offset2}
        fi.close

    def getfileobject(self):
        if self.filemode == 'hdd':
            fi = open(self.path, 'rb')
        elif self.filemode == 'bytesfromfile':
            with open(self.path, 'rb') as f:
                data = f.read(17428)
            fi = io.BytesIO(data)
        elif self.filemode == 'mem':
            #print(streamdata)
            fi = io.BytesIO(self.streamdata)
        else:
            exit('filemode', self.filemode, ' not supported')
        return fi

    def getparamsfromblock(self, offset, length, full=False):
        '''Read all parameters in a block at binary <length> and <offset> and return as dictionary. On request also include binary length and offset of that parameter.'''
        params = {}
        i=0
        test = True
        fullblock = []
        fi = self.getfileobject()
        with fi as f: #with open(self.path, 'rb') as f:
            while test:
                f.seek(offset+i) # goto block offset
                s = f.read(8) # read 8 bytes
                para, thistype, length = struct.unpack('4s2H', s) # unpack to get info on how to unpack block
                if full:
                    fullblocktmp = [para, thistype, length, offset+i]
                i+=8
                if struct.unpack('4c', para)[-1]==b'\x00': #get null terminating string
                    para=para[:-1]
                else: pass
                if para[:3] != b'END' and length>0: # if not empty block
                    f.seek(offset+i)
                    data = f.read(2*length)
                    i+=2*length
                    try:
                        if thistype == 0:
                            val = struct.unpack('%1ii'%(len(data)/4), data)[0]
                        elif thistype == 1:
                            val = struct.unpack('%1id'%(len(data)/8), data)[0]
                        elif thistype >= 2 and thistype <=4:
                            t = struct.unpack('%1is'%(2*length), data)[0].decode('ISO-8859-1')
                            t2 = ''
                            for ji in t: # deal with zeros in byte array
                                if ji!='\x00' and type(ji)==str: # in python2 you might want to add ... or type(ji)=='unicode'):
                                    t2 += ji
                                else:
                                    break
                            val=t2
                        else:
                            val= '[read error]'
                        params[para.decode()] = val
                        if full:
                            fullblocktmp.append(val)
                            fullblock.append(fullblocktmp)
                    except Exception as e:
                        print('Exception in getparamsfromblock')
                        self.log.append(e)
                        print (e)
                else:
                    test = False
        if full:
            return fullblock
        else:
            return params

    def read_header(self):
        '''Read the header and return as a dictionary.'''
        self.log.append('Reading Header ...')
        self.read_structure()
        self.header = {}
        for block in self.fs.keys():
            if block[:10]!='Data Block' and self.fs[block]['length']>0: # if not data block and not empty, try reading header info
                if 'unknown' in block or 'something' in block:
                    pass
                else:
                    try:
                        self.log.append('Reading Header Block: '+block)
                        self.header[block] = self.getparamsfromblock(self.fs[block]['offset'], self.fs[block]['length'], full=False)
                    except Exception as e:
                        print(e)
                        self.log.append(e)
            else: pass
        return 0

    def get_block(self, pointer, length):
        '''Get data block from file object at <pointer> with length <length>.'''
        self.log.append('Getting data block at '+str(pointer)+' with length '+str(length))
        fi = self.getfileobject()
        with fi as f: 
            f.seek(pointer)
            dat = np.array(struct.unpack('%1if'%(length), f.read(length*4)))
        return dat

    def get_datablocks(self, block):
        '''Read a datablock named <block> and retrieve x- and y-axis np.arrays from it.'''
        #t = time.time()
        self.log.append('Getting data blocks')
        yax = np.array(self.get_block(self.search_block(block)['offset'], self.search_block(block)['length']))
        #print(block)
        if block == 'Data Block IgSm' or block == 'Data Block':
            self.log.append('Getting ifg data block')
            # crude estimate of opd axis, only for illustratiion purposes, zpd's not included in calculation, and triangular apod. assumption -> 0.9
            xax = np.linspace(0,2*0.9/float(self.header['Acquisition Parameters']['RES']), len(yax))
        if block == 'Data Block SpSm':
            self.log.append('Getting spc data block')
            # calculate wavenumber axis for spectrum from frequencies of first and last point stored in header
            xax = np.linspace(self.header['Data Parameters SpSm']['FXV'], self.header['Data Parameters SpSm']['LXV'], len(yax))
        if block == 'Data Block ScSm':
            self.log.append('Getting spc data block')
            xax = np.linspace(self.header['Data Parameters ScSm']['FXV'], self.header['Data Parameters ScSm']['LXV'], len(yax))
        if block == 'Data Block TrSm':
            self.log.append('Getting trm data block')
            xax = np.linspace(self.header['Data Parameters TrSm']['FXV'], self.header['Data Parameters TrSm']['LXV'], len(yax))
        if block == 'Data Block PhSm':
            self.log.append('Getting pha data block')
            xax = np.linspace(self.header['Data Parameters PhSm']['FXV'], self.header['Data Parameters PhSm']['LXV'], len(yax))
        return xax, yax

    def test_if_ftsfile(self):
        '''Check the initialized filename for FTS magic number.'''
        self.log.append('testing if FTS file')
        # same 4-byte binary representation found on all valid FTS files ... must be magic
        ftsmagicval = b'\n\n\xfe\xfe'
        try:
            fi = self.getfileobject()
            with fi as f: #with open(self.path, 'rb') as f:
                f.seek(0)
                magic = f.read(4)
            if magic==ftsmagicval:
                if self.verbose:
                    self.log.append('Identified '+self.path+' as FTS file ...')
                self.status=True
                self.isftsfile = True
            else:
                self.log.append('Bad Magic found in '+self.path)
                print('Bad Magic in ', self.path)
                self.status=False
                self.isftsfile = False
        except Exception as e:
            self.log.append(e)
            self.status=False
            self.isftsfile = False

    def search_block(self, blockname):
        '''Searches a <blockname> within the identifies FTS file structure. Returns dictionary entry of the block <blockname>.'''
        if blockname in list(self.fs.keys()):
            return self.fs[blockname]
        else:
            self.log.append('Could not find '+str(blockname)+' in self.fs.keys()')

    def has_block(self, blockname):
        '''Check if <blockname> is present in ftsreader.fs'''
        if blockname in self.fs.keys():
            return True
        else:
            return False

    def __init__(self, path, verbose=False, getspc=False, getifg=False, gettrm=False, getpha=False, getslices=False, filemode='hdd', streamdata=None):
        self.log = []
        self.status = True
        self.verbose = verbose
        self.path = path
        self.filemode = filemode
        self.streamdata = streamdata
        if self.verbose:
            print('Initializing ...')
        self.log.append('Initializing')
        try:
            if path.rfind('/')>0:
                self.folder = path[:path.rfind('/')]
                self.filename = path[path.rfind('/')+1:]
            else:
                self.folder = './'
                self.filename = path
            if not getslices:
                self.test_if_ftsfile()
            if self.status:
                if not getslices:
                    self.read_header()
                else: pass
                # get ifg if requested
                if getifg and self.has_block('Data Block IgSm'):
                    self.ifgopd, self.ifg = self.get_datablocks('Data Block IgSm')
                else:
                    self.log.append('No Interferogram requested or not found ... skipping.')
            else: raise(ValueError('Does not seem to be an FTS file ... skipping'))
            if self.verbose and not self.status:
                self.log.append('An error occured.')
                print('An error occured.')
        except Exception as e:
            self.log.append('Problem with '+str(e))
            print('Error while processing '+path+' ... check self.log or do self.print_log()')

def load_yaml(yamlfile):
    # load config file
    with open(yamlfile, 'r') as f:
        yamlcontent = yaml.safe_load(f)
    return yamlcontent

def smooth_ifg(o, lwn=15798.022, cutoff=3700, l0=4000):
    pkl = o.header['Instrument Parameters']['PKL']
    # zero ifg
    ifg0 = o.ifg[int(pkl-l0/2):int(pkl+l0/2)]
    ifgz = ifg0-np.median(ifg0)
    p = 5 # percent apodization region at beginning and end of IFG
    l = int(l0*p/100.0)
    # create hanning apodization function and apply to ifg
    a1 = np.ones(l0)
    a1[:l] = ((np.cos(np.pi*np.arange(l)/l)+1)**2/4)[::-1]
    a1[-l:] = ((np.cos(np.pi*np.arange(l)/l)+1)**2/4)
    ifga = ifgz*a1
    # get spc via complex fft of ifg
    spc = np.fft.fft(ifga)
    # calculate wvn axis, LWN is taken from opus header info
    wvn = np.fft.fftfreq(int(len(spc)),0.5/lwn)[:int(len(spc)/2)]
    # determine index of cut-off wavenumber
    l = len(wvn[wvn<cutoff])
    ys = spc.copy()
    # set everything in spectrum between larger than cutoff wavenumber to complex 0, same at the end of the array (mirrored spc)
    #ys[l:-l] = 0.0+0j
    # define and apply Hann window function
    sfunc = lambda nu, cutoff: np.cos(np.pi*nu/(2*cutoff))**2
    a2 = np.ones(len(ys))*(0+0j)
    a2[:l] = sfunc(wvn[:l], cutoff)
    # apply to mirrored part of spc as well. careful to use the correct order of wvn here
    a2[-l:] = sfunc(wvn[:l][::-1], cutoff)
    # apply apodization
    ys = a2*ys
    # calculate inverse fft of apodized spc, discarding imaginary part of reverse fft
    return np.fft.ifft(ys).real, ys, a1, wvn

class Preview125(QtWidgets.QMainWindow):
    """ A preview of measurements with the IFS125 in idle mode. Similar to the common Check Signal 
    functionality, but with control of the ifg and header data.
    
    ! Before usage: Adjust config.yaml to your situation !"""
     
    def start_measurement(self):
        if self.running:
            print('Sending measure command')
            requests.get(self.url_measure)
        else: pass

    def stop_measurement(self):
        print('Sending stop command')
        requests.get(self.url_measurestop)

    def get_status(self):
        # find status info in response to request to ifs
        stat = requests.get(self.stat_htm)
        i1 = stat.text.rfind('ID=MSTCO')
        i2 = stat.text.find('<', i1)
        status = stat.text[i1 + 9:i2]
        return status
    
    def get_preview(self):
        if self.running:
            # 
            self.start_measurement()
            status = 'SCN'
            while status != 'IDL':
                # repeat requests until IDL
                status = self.get_status()
            if status=='IDL':
                # find download link
                data = requests.get(self.data_htm)
                i1 = data.text.find('A HREF=')
                i2 = data.text.find('">', i1)
                # download data from ifs
                data = requests.get('/'.join((self.url_ftir,data.text[i1+9:i2])))
                # read in opus format
                self.preview = ftsreader('', verbose=False, getifg=True, filemode='mem', streamdata=data.content)
                self.ifg_s, self.spc_apodized, self.apo, self.apo_wvn = smooth_ifg(self.preview, lwn=self.config['lwn'],  cutoff=self.config['cutoff'], l0=self.config['npt'])
                #self.calc_spc()
                #print('all 0? ', np.all(self.ifg_s==0))
                self.spc = np.fft.fft(self.preview.ifg)
                self.wvn = np.fft.fftfreq(int(len(self.spc)),0.5/self.preview.header['Instrument Parameters']['LWN'])[:int(len(self.spc)/2)]
            else: pass
        else:
            pass
        
    def startpreview(self):
        print('Check Signal')
        self.running=True
        self._timer1.start()
            
    def stoppreview(self):
        print('Stop Measurements')
        self.running=False
        self._timer1.stop()
        self.stop_measurement()
    
    def calc_spc(self):
        self.spc = np.fft.fft(self.preview.ifg)
        self.wvn = np.fft.fftfreq(int(len(self.spc)),0.5/self.preview.header['Instrument Parameters']['LWN'])[:int(len(self.spc)/2)]
        self.ifg_s = self.preview.ifg
    
    def zpd(self):
        # use peak location from header
        self.zpdindex = self.preview.header['Instrument Parameters']['PKL']

    def zpd_minmax(self):
        # using mean between indices of max and min values of ifg
        self.zpdindex = int(round(np.mean([np.argmin(self.preview.ifg), np.argmax(self.preview.ifg)]))) 

    def _update(self):
        # get measurement data
        self.get_preview()
        self.run +=1
        # update plots
        y = np.abs(self.spc[10:int(len(self.spc)/2)])
        self._line1.set_data((self.wvn[10:], y/np.max(y)))
        #if self.run == 2:
        #    self._dynamic_ax2.set_xlim(self.config['spc_plot_xlim'])
        #    self._dynamic_ax2.set_ylim(np.min(y)*1.2, np.max(y)*1.2)
        self._line1.figure.canvas.draw()
        if self.scaledifgaxes:
            y1 = self.preview.ifg-np.mean(self.preview.ifg)
            self._line2.set_data((np.arange(len(self.preview.ifg)), y1/np.max(np.abs(y1))))
        else:
            self._line2.set_data((np.arange(len(self.preview.ifg)), self.preview.ifg))            
        #if self.run == 2:
        #    #self._dynamic_ax2.set_xlim(0,4000)
        #    self._dynamic_ax2.set_ylim(np.min(self.preview.ifg)*1.2, np.max(self.preview.ifg)*1.2)
        self._line2.figure.canvas.draw()
        if self.scaledifgaxes:
            y2 = self.ifg_s-np.mean(self.ifg_s[self.config['zpd_interval'][0]:self.config['zpd_interval'][1]])
            self._line3.set_data((np.arange(len(self.preview.ifg)), y2/np.max(np.abs(y2[self.config['zpd_interval'][0]:self.config['zpd_interval'][1]]))))
        else:
            self._line3.set_data((np.arange(len(self.ifg_s)), self.ifg_s))
        #if self.run == 2:
        #    #self._dynamic_ax2.set_xlim(0,4000)
        #    self._dynamic_ax3.set_ylim(np.min(self.ifg_s)*1.2, np.max(self.ifg_s)*1.2)
        self._line3.figure.canvas.draw()
        
    def clickBox(self, b):
        if b.isChecked() == True:
            self.scaledifgaxes = True
            print('IFG y-axis scaling: ON')
        else:
            self.scaledifgaxes = False
            print('IFG y-axis scaling: OFF')
        
    def __init__(self):
        # init everyting
        super().__init__()
        # define global variables              
        self.config = load_yaml('config.yaml')
        self.run = 0
        self.running=False
        self.npt = self.config['npt']
        self.site = self.config['selected_site']
        self.siteconfig = self.config[self.site]
        self.url_ftir = 'http://'+self.siteconfig['ip']
        self.url_measure = '/'.join((self.url_ftir, self.siteconfig['preview_commands']))
        self.url_measurestop = '/'.join((self.url_ftir, self.siteconfig['shutdown_commands']))
        self.stat_htm = '/'.join((self.url_ftir,'stat.htm'))
        self.data_htm = '/'.join((self.url_ftir, 'datafile.htm'))
        self.title = '125HR Preview Idle Mode'
        self.setWindowTitle(self.title)
        self.resize(800, 700)
        #
        #
        self._main = QtWidgets.QWidget()
        self.setCentralWidget(self._main)
        layout = QtWidgets.QVBoxLayout(self._main)
        # setup matplotlib canvases
        dynamic_canvas1 = FigureCanvas(Figure(figsize=(5, 3)))
        layout.addWidget(NavigationToolbar(dynamic_canvas1, self))
        layout.addWidget(dynamic_canvas1)
        #
        #self.box = QCheckBox('IFG y-axis scaled',self)
        #self.box.setChecked(False)
        self.scaledifgaxes = False
        #self.box.stateChanged.connect(lambda : self.clickBox(self.box))
        #layout.addWidget(self.box)
        #
        dynamic_canvas2 = FigureCanvas(Figure(figsize=(5, 3)))
        layout.addWidget(dynamic_canvas2)
        layout.addWidget(NavigationToolbar(dynamic_canvas2, self))
        self._dynamic_ax1 = dynamic_canvas1.figure.subplots()
        self._dynamic_ax1.set_title('Spectrum preview')
        self._dynamic_ax1.set_xlim(self.config['spc_plot_xlim'])
        self._dynamic_ax1.set_ylim(0,1)
        self._line1, = self._dynamic_ax1.plot(np.linspace(3000, 11000, self.npt), np.zeros(self.npt), 'b-')
        self._dynamic_ax2 = dynamic_canvas2.figure.subplots()
        self._dynamic_ax2.set_title('Raw Interferogram preview')
        self._dynamic_ax3 = self._dynamic_ax2.twinx()
        self._dynamic_ax3.set_title('Smoothed Interferogram preview')
        self._dynamic_ax2.set_xlim(self.config['zpd_interval'])
        self._dynamic_ax2.set_ylim(-1,1)
        self._line2, = self._dynamic_ax2.plot(np.arange(self.npt), np.zeros(self.npt), '-', color='grey')
        self._line3, = self._dynamic_ax3.plot(np.arange(self.npt), np.zeros(self.npt), 'k-')
        # Setup timer to repeat measurement cycle
        self._timer1 = dynamic_canvas1.new_timer(self.config['refreshrate']*1000)
        self._timer1.add_callback(self._update)
        self._timer1.stop()        
        # start button
        self.startButton = QPushButton(self)
        self.startButton.setText('Check Signal')          #text
        self.startButton.setShortcut('Space')  #shortcut key
        self.startButton.clicked.connect(self.startpreview)
        self.startButton.setToolTip('starting timer to perform low-res measurements; Shortcut: [Space]')
        layout.addWidget(self.startButton)
        # stop button
        self.stopButton = QPushButton(self)
        self.stopButton.setText('Stop Measurements')
        self.stopButton.setShortcut('Esc')
        self.stopButton.clicked.connect(self.stoppreview)
        self.stopButton.setToolTip('stop the timer and thus end preview measurements; Shortcut: [Esc]')
        layout.addWidget(self.stopButton)
    
if __name__ == "__main__":
    if len(sys.argv)==1:
        qapp = QtWidgets.QApplication.instance()
        if not qapp:
            qapp = QtWidgets.QApplication(sys.argv)
        app = Preview125()
        app.show()
        app.activateWindow()
        app.raise_()
        qapp.exec()
    else:
        fname = sys.argv[1]
        config = load_yaml('config.yaml')
        o = ftsreader(fname, getifg=True)
        cutoff = config['cutoff']
        pkl = o.header['Instrument Parameters']['PKL']
        l0 = config['npt']
        lwn = config['lwn']
        ifg = o.ifg[int(pkl-l0/2):int(pkl+l0/2)]
        ifgs, ys, a, wvn = smooth_ifg(o, lwn=lwn,  cutoff=config['cutoff'], l0=l0)
        #
        fig, (ax1, ax2) = plt.subplots(nrows=2, sharex=True)
        ax1.set_title(fname+' FWD')
        ax1.set_xlim(0,config['npt'])
        ax1.plot(ifg, label='original ifg')
        ax1.plot(a/10, label='apodization function (x 1/10)')
        ax1.plot(a*(ifg-np.median(ifg)), label='hann apodized ifg')
        ax1.plot(ifgs, label='smoothed ifg')
        ax1.legend(loc='lower center', ncol=2)
        #ax2.set_xlim(l0/2-fitwindowsize,l0/2+fitwindowsize)
        ax2.set_ylim(-0.00002,0.0001)
        ax2.plot(ifgs, label='smoothed ifg')
        #ax2.plot(xfit, fitfunc(xfit, *popt), label='linear fit: DIP size = %.3E'%(np.max(np.abs(yfit-fitfunc(xfit, *popt)))))
        ax2.set_xlabel('ifg index')
        ax2.legend(loc='upper left', ncol=2)
        #fig.savefig('DIP_live_test_'+fname+'_'+ifgn+'_ifg.png', dpi=200)
        plt.show()










