from struct import pack, unpack

BULK_WRITE_ENDPOINT = 0x2
BULK_READ_ENDPOINT = 0x1
BULK_READ_LENGTH = 64

COMMAND_CODE = b'\xaa'
COMMAND_TYPE_LONG = b'\x42'

RESPONSE_CODE = b'\x55'
RESPONSE_STATUS_SUCCES = b'\x5a'

PS2_CARD_TYPE = 2
PAGE_LENGTH = 0x210 # PS2
PS2_CARD_SIZE = 0x840210

PS1_CARD_TYPE = 1
FRAME_LENGTH = 0x80 # PS1
PS1_CARD_SIZE = 0x20000

PS1_COMMAND_TAIL = b'\x00' * 0x86 # 0x36 + 0x40 + 0x16

CARD_SIZE_DICT = {
    PS1_CARD_TYPE: PS1_CARD_SIZE,
    PS2_CARD_TYPE: PS2_CARD_SIZE,
}

CARD_PAGE_DICT = {
    PS1_CARD_TYPE: FRAME_LENGTH,
    PS2_CARD_TYPE: PAGE_LENGTH,
}

def hexdump(data):
    return ' '.join('%02x' % x for x in data)

def _padCommand(command, padding=2):
    return command + b'\x00' * padding

def _stripResponse(response, padding=2):
    stuffing = response[:-padding]
    assert stuffing == b'\xff' * len(stuffing), hexdump(stuffing)
    return response[-padding:]

class PlayStationMemoryCardReader(object):
    def __init__(self, usb_device, authenticator):
        self._usb_device = usb_device
        self._authenticator = authenticator

    # Read/write command helpers
    def _usbRead(self):
        result = self._usb_device.bulkRead(BULK_READ_ENDPOINT, BULK_READ_LENGTH)
        #print '<', hexdump(result)
        return result

    def _responseRead(self):
        result = self._usbRead()
        if result[0:1] != RESPONSE_CODE:
            raise ValueError('Received data is not a valid response: %s' % (
              hexdump(result), ))
        return result[1:]

    def _longResponseRead(self):
        result = []
        append = result.append
        response = self._responseRead()
        response_code = response[0:1]
        if response_code == RESPONSE_STATUS_SUCCES:
            response_length = unpack('<h', response[1:3])[0]
            data = response[3:]
            data_length = len(data)
            append(data)
            while data_length < response_length:
                response = self._usbRead()
                data_length += len(response)
                append(response)
        return response_code, b''.join(result)

    def _usbWrite(self, data):
        #print '>', hexdump(data)
        self._usb_device.bulkWrite(BULK_WRITE_ENDPOINT, data)

    def _commandWrite(self, data):
        self._usbWrite(COMMAND_CODE + data)

    def _longCommandWrite(self, data):
        self._commandWrite(COMMAND_TYPE_LONG + pack('<h', len(data)) + data)

    # Identified commands
    def getCardType(self):
        """
          Known return values:
           0: No card
           1: PS1 card
           2: PS2 card
        """
        self._commandWrite(b'\x40')
        response = self._responseRead()
        assert len(response) == 1, hexdump(response)
        return response[0]

    def isAuthenticated(self):
        """
          Return values:
            False: Card reader is in limited mode (PS1 cards only).
            True: Card reader allows full access (PS1 & PS2 card access).
        """
        self._longCommandWrite(_padCommand(b'\x81\x11'))
        response_code, data = self._longResponseRead()
        if response_code == b'\xaf':
            result = False
        else:
            assert response_code == RESPONSE_STATUS_SUCCES, hexdump(
              response_code)
            response = _stripResponse(data)
            assert response == b'\x2b\x55', hexdump(response)
            result = True
        return result

    # PS1
    def readFrame(self, frame_number):
        """
          Read a frame from PS1 card.
        """
        # TODO:
        # - check frame number
        encoded_frame_number = pack('>H', frame_number)
        self._longCommandWrite(b'\x81\x52\x00\x00' + \
          encoded_frame_number + PS1_COMMAND_TAIL)
        response_code, data = self._longResponseRead()
        assert response_code == RESPONSE_STATUS_SUCCES, hexdump(response_code)
        #data_header = data[:0xa]
        #assert data_header == b'\xff\x00\x5a\x5d\x00\x00\x5c\x5d' + \
        #  encoded_frame_number, hexdump(data_header)
        # XXX: Sometimes also b'\xff\x08\x5a\x5d\x00\x00\x5c\x5d' + \
        #   encoded_frame_number
        #data_tail = data[-2:] # Unknown content (checksum ?)
        data = data[0xa:-2]
        assert len(data) == FRAME_LENGTH, '%i: %s' % (len(data), hexdump(data))
        return data

    def writeFrame(self, frame_number, data):
        """
          Write a frame to PS1 card.
        """
        assert len(data) == FRAME_LENGTH
        # TODO:
        # - check frame number
        encoded_frame_number = pack('>H', frame_number)
        self._longCommandWrite(''.join((
          b'\x81\x57\x5a\x5d',
          encoded_frame_number,
          data,
          b'\x00', # XXX: seems to be some kind of checksum, but data seems written
                   # even without computing it
          b'\x5c\x5d\x47',
        )))
        response_code, response_data = self._longResponseRead()
        assert response_code == RESPONSE_STATUS_SUCCES, hexdump(response_code)
        assert response_data[:4] == b'\xff\x00\x5a\x5d',  hexdump(response_data[:4])
        assert response_data[4] == 0x00, hexdump(response_data[4:5])
        assert response_data[5:7] == encoded_frame_number, \
          hexdump(response_data[5:6])
        assert response_data[7:-3] == data, (hexdump(response_data[7:-3]), \
          hexdump(data))
        # XXX: the last byte of response changes from refernce dumps.
        # This is probably because of the incorrect checksum.
        #assert response_data[-3:] == '\x5c\x5d\x47', hexdump(
        #  response_data[-3:])

    # PS2
    def readPage(self, page_number):
        """
          Read a page from PS2 card.
        """
        # TODO:
        # - check page number
        self.authenticate()
        self._commandWrite(b'\x52\x03' + pack('<I', page_number) + b'\x55\x2b')
        response_code, data = self._longResponseRead()
        assert response_code == RESPONSE_STATUS_SUCCES, hexdump(response_code)
        assert len(data) == PAGE_LENGTH, '%i: %s' % (len(data), hexdump(data))
        return data

    def writePage(self, page_number, data):
        """
          Write a page to PS2 card.
        """
        assert len(data) == PAGE_LENGTH
        # TODO:
        # - check page number
        self.authenticate()
        self._commandWrite(''.join((
          b'\x57\x03',
          pack('<I', page_number),
          data,
          b'\x55\x2b'
        )))
        response = self._responseRead()
        assert len(response) == 1, hexdump(response)
        assert response == RESPONSE_STATUS_SUCCES, hexdump(response)

    def getRandomNumber(self, seq_number=4):
        """
          This number is used in PS2 card authentication process.
          Host must convert it and send conversion result back to device later to
          prove it is an authorised host. This job must be done by authenticator
          instance given at construction time.
        """
        return self.__recv_81f0(seq_number, 9)

    def sendAuthPart1(self, data, seq_number=6):
        self.__send_81f0(seq_number, data)

    def sendAuthPart2(self, data, seq_number=7):
        self.__send_81f0(seq_number, data)

    def sendAuthPart3(self, data, seq_number=0xb):
        self.__send_81f0(seq_number, data)

    def recvAuthPart1(self, seq_number=0xf):
        return self.__recv_81f0(seq_number, 9)

    def recvAuthPart2(self, seq_number=0x11):
        return self.__recv_81f0(seq_number, 9)

    def recvAuthPart3(self, seq_number=0x13):
        return self.__recv_81f0(seq_number, 9)

    # Generic & unidentified commands
    def __81f0(self, seq_number):
        self._longCommandWrite(_padCommand(b'\x81\xf0' + pack('b', seq_number)))
        response_code, data = self._longResponseRead()
        result = response_code == RESPONSE_STATUS_SUCCES
        if result:
            response = _stripResponse(data)
            assert response == b'\x2b\xff', hexdump(response)
        return result

    def __recv_81f0(self, seq_number, length):
        padding = length + 2
        self._longCommandWrite(_padCommand(b'\x81\xf0' + pack('b', seq_number),
          padding=padding))
        response_code, data = self._longResponseRead()
        assert response_code == RESPONSE_STATUS_SUCCES, hexdump(response_code)
        response = _stripResponse(data, padding)
        assert response[0] == 0x2b and response[-1] == 0xff, hexdump(
          response)
        return response[1:-1]

    def __send_81f0(self, seq_number, data):
        assert len(data) == 9, hexdump(data)
        self._longCommandWrite(_padCommand(b'\x81\xf0' + pack('b',
          seq_number) + data))
        response_code, data = self._longResponseRead()
        assert response_code == RESPONSE_STATUS_SUCCES, hexdump(response_code)
        response = _stripResponse(data)
        assert response == b'\x2b\xff', hexdump(response)

    def __8128(self):
        self._longCommandWrite(_padCommand(b'\x81\x28', padding=3))
        response_code, data = self._longResponseRead()
        assert response_code == RESPONSE_STATUS_SUCCES, hexdump(response_code)
        response = _stripResponse(data, padding=3)
        assert response == b'\x2b\xff\xff', hexdump(response)

    def __8127(self):
        self._longCommandWrite(_padCommand(b'\x81\x27\x55'))
        response_code, data = self._longResponseRead()
        assert response_code == RESPONSE_STATUS_SUCCES, hexdump(response_code)
        response = _stripResponse(data)
        assert response == b'\x2b\x55', hexdump(response)

    def __8126(self):
        self._longCommandWrite(_padCommand(b'\x81\x26', padding=11))
        response_code, data = self._longResponseRead()
        assert response_code == RESPONSE_STATUS_SUCCES, hexdump(response_code)
        response = _stripResponse(data, padding=11)
        assert response[0] == 0x2b and response[-1] == 0x55, hexdump(
          response)
        return response[1:-1]

    def __8158(self):
        self._longCommandWrite(b'\x81\x58\x00\x00\x00')
        response_code, data = self._longResponseRead()
        assert response_code == b'\xaf', hexdump(response_code)

    def __81f3(self):
        self._longCommandWrite(_padCommand(b'\x81\xf3\x00'))
        response_code, data = self._longResponseRead()
        assert response_code == RESPONSE_STATUS_SUCCES, hexdump(response_code)
        response = _stripResponse(data)
        assert response == b'\x2b\xff', hexdump(response)

    def __81f7(self):
        self._longCommandWrite(_padCommand(b'\x81\xf7\x01'))
        response_code, data = self._longResponseRead()
        assert response_code == RESPONSE_STATUS_SUCCES, hexdump(response_code)
        response = _stripResponse(data)
        assert response == b'\x2b\xff', hexdump(response)

    # IO helpers
    def read(self, offset, length):
        """
          Read data starting at <offset> bytes for <length> bytes.

          offset & length can be of arbitrary values, as long as they fit in
          memory card space.
        """
        card_type = self.getCardType()
        if card_type == 1:
            read = self.readFrame
        elif card_type == 2:
            read = self.readPage
        else:
            raise ValueError('No/unknown card (%02x)' % (card_type, ))
        block_length = self._getPageSize(card_type)
        max_length = self._getSize(card_type)
        if offset + length > max_length:
            raise ValueError('Trying to read out of card.')
        result = []
        append = result.append
        current_block, start_offset = divmod(offset, block_length)
        result_len = -start_offset
        while result_len < length:
            data = read(current_block)
            assert len(data) == block_length, len(data)
            result_len += block_length
            append(data)
            current_block += 1
        result[0] = result[0][start_offset:]
        if result_len > length:
            result[-1] = result[-1][:length - result_len]
        return b''.join(result)

    def write(self, offset, data):
        """
          Write <data> starting at <offset>.

          offset & data length can be of arbitrary values, as long as they fit in
          memory card space. This function will take care reading existing block
          data if write does not start and/or stop on an underlying block boundary.
        """
        card_type = self.getCardType()
        if card_type == 1:
            read = self.readFrame
            write = self.writeFrame
        elif card_type == 2:
            read = self.readPage
            write = self.writePage
        else:
            raise ValueError('No/unknown card (%02x)' % (card_type, ))
        block_length = self._getPageSize(card_type)
        max_length = self._getSize(card_type)
        if offset + len(data) > max_length:
            raise ValueError('Trying to write out of card.')
        current_block, start_offset = divmod(offset, block_length)
        if start_offset:
            data = read(current_block)[:start_offset] + data
        while len(data) >= block_length:
            to_write, data = data[:block_length], data[block_length:]
            write(current_block, to_write)
            current_block += 1
        data_len = len(data)
        if data_len:
            write(current_block, data + read(current_block)[data_len:])

    @staticmethod
    def _getSize(card_type):
        return CARD_SIZE_DICT.get(card_type)

    def getSize(self):
        return self._getSize(self.getCardType())

    @staticmethod
    def _getPageSize(card_type):
        return CARD_PAGE_DICT.get(card_type)

    def getPageSize(self):
        return self._getPageSize(self.getCardType())

    # Authentication
    def authenticate(self):
        """
          Authentication scenario.
          The meaning of most of this is unknown, but it is enough to have it
          work.

          Basicaly, authentication is protected by 2 mechanisms:
          - 1-way (maybe even 2-way) identification
            Device generates a random seed and checks value sent by host.
            This is to prevent replay attacks.
            As we want to access data whatever the device is, we don't have to
            care about the existence of the 2nd part of auth if it exists.
          - Upper time limit on exchanges
            If hosts takes too long between USB queries, the device refuses the
            auth.
            This is to protect against rogue implementations of authentication
            mechanism, which might take too long to compute responses.

          But there is a huge weakness in the implementation of this otherwise
          robust process: pseudo-random number generator in the device is extremely
          weak, making the whole process vulnerable to replay attacks.
          Measures found that:
          - Out of 1000 random number generations, one of 2 values is generated 50%
            of the time. So knowing the answer for 2 seeds gives a 50% chance of
            auth success.
          - Sadly, the PRNG is seeded upon replugging, so it's not possible to
            go off with a 2-entry rainbow table.
        """
        while not self.isAuthenticated():
            # ?
            self.__81f3()
            self.__81f7()
            self.__81f0(0)
            # card reader serial ?
            self.__recv_81f0(1, 9)
            self.__recv_81f0(2, 9)
            # ?
            self.__81f0(3)
            # Random value
            seed = self.getRandomNumber()
            answer_list = self._authenticator.authenticate(seed)
            # ?
            if not self.__81f0(5):
                print('Auth timeout, retrying...')
                continue
            # First answer
            self.sendAuthPart1(answer_list[0])
            # Second answer
            self.sendAuthPart2(answer_list[1])
            # ?
            self.__81f0(0x8)
            self.__81f0(0x9)
            self.__81f0(0xa)
            # Third answer
            self.sendAuthPart3(answer_list[2])
            # ?
            self.__81f0(0xc)
            self.__81f0(0xd)
            self.__81f0(0xe)
            # Receive device auth ?
            self.recvAuthPart1()
            self.__81f0(0x10)
            self.recvAuthPart2()
            self.__81f0(0x12)
            self.recvAuthPart3()
            self.__81f0(0x14)
            # ?
            self.__8128()
            self.__8127()
            self.__8126()
            # Now, we must be authenticated
            if not self.isAuthenticated():
                raise ValueError('Authentication went to the end, but we ' \
                  'are not authenticated !')
